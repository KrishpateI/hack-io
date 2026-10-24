import * as fs from 'fs';
import * as path from 'path';
import {
  Annotations, CfnOutput, Duration, RemovalPolicy, Stack, StackProps,
} from 'aws-cdk-lib';
import * as apigw from 'aws-cdk-lib/aws-apigatewayv2';
import { HttpLambdaIntegration, WebSocketLambdaIntegration } from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as appsync from 'aws-cdk-lib/aws-appsync';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';
import { SafePathConfig } from './config';
import { DataStack } from './data-stack';

export const API_MEMORY_MB = 3008; // Graph.load of the downtown graph peaks ~1 GB (docs/aws-deployment.md)
export const API_TIMEOUT_SECONDS = 29; // API Gateway's integration limit is 29-30 s
export const THROTTLE_RATE = 50; // requests per second (Req 31)
export const THROTTLE_BURST = 100;

/** Tables the sweeper's run_sweep() touches (expire reports, scrub locations, purge TTL rows) plus publish fan-out. */
export const SWEEPER_TABLES = [
  'incidents', 'overlays', 'overlay_meta', 'notices', 'reports', 'report_votes', 'messages',
  'message_index', 'checkins', 'checkin_locations', 'ratelimits', 'events', 'audit', 'connections',
] as const;

/** The websocket function only registers/unregisters connection rows. */
export const WEBSOCKET_TABLES = ['connections'] as const;

export interface ApiStackProps extends StackProps {
  readonly config: SafePathConfig;
  readonly data: DataStack;
}

/**
 * API layer: Lambda functions (API, sweeper, optional websocket), HTTP API, realtime, optional Cognito.
 *
 * Lambda package: `python scripts/package_lambda.py` writes infrastructure/aws/build/safepath-lambda.zip.
 * Override the location with `-c safepath:lambdaZip=<path>`. When the zip is absent (offline synth,
 * unit tests) a tiny stub from `lambda-stub/` is used so `cdk synth` still works; a synth warning is
 * emitted and the stack output `CodeSource` says `stub`. Set `-c safepath:requireLambdaPackage=true`
 * to make synth fail instead (use it for real deployments).
 *
 * IAM: one default role per function (AWSLambdaBasicExecutionRole for logs only); everything else is
 * an explicit grant on a named table / bucket prefix / API ARN. No wildcard actions or resources.
 */
export class ApiStack extends Stack {
  public readonly apiFunction: lambda.Function;
  public readonly sweeperFunction: lambda.Function;
  public readonly websocketFunction?: lambda.Function;
  public readonly httpApi: apigw.HttpApi;
  public readonly webSocketApi?: apigw.WebSocketApi;
  public readonly eventApi?: appsync.EventApi;
  public readonly userPool?: cognito.UserPool;
  public readonly userPoolClient?: cognito.UserPoolClient;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);
    const { config, data } = props;
    const ctx = (k: string) => this.node.tryGetContext(k);

    // ---------------------------------------------------------------- code package
    const zipPath = path.resolve(String(ctx('safepath:lambdaZip') ?? path.join(__dirname, '..', 'build', 'safepath-lambda.zip')));
    const havePackage = fs.existsSync(zipPath);
    if (!havePackage && String(ctx('safepath:requireLambdaPackage')).toLowerCase() === 'true') {
      throw new Error(`Lambda package not found at ${zipPath}; run "python scripts/package_lambda.py" first.`);
    }
    if (!havePackage) {
      Annotations.of(this).addWarningV2('safepath:lambda-stub',
        `Lambda package ${zipPath} not found; using the placeholder stub. Run scripts/package_lambda.py before deploying.`);
    }
    const code = havePackage ? lambda.Code.fromAsset(zipPath) : lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda-stub'));
    new CfnOutput(this, 'CodeSource', { value: havePackage ? 'package' : 'stub' });

    // ---------------------------------------------------------------- secrets (never in plain env)
    const tokenSecret = new secretsmanager.Secret(this, 'TokenSecret', {
      secretName: `${config.prefix}/token-secret`,
      description: 'SafePath guest/demo token signing key (SIMULATED sandbox)',
      generateSecretString: { passwordLength: 48, excludePunctuation: true },
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const aliasSecret = new secretsmanager.Secret(this, 'AliasSecret', {
      secretName: `${config.prefix}/alias-secret`,
      description: 'SafePath reporter-alias HMAC key',
      generateSecretString: { passwordLength: 48, excludePunctuation: true },
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // ---------------------------------------------------------------- Cognito (optional)
    if (config.useCognito) {
      this.userPool = new cognito.UserPool(this, 'UserPool', {
        userPoolName: `${config.prefix}-users`,
        selfSignUpEnabled: false,
        signInAliases: { email: true },
        removalPolicy: RemovalPolicy.DESTROY,
      });
      this.userPoolClient = this.userPool.addClient('AppClient', {
        userPoolClientName: `${config.prefix}-app`,
        generateSecret: false, // public SPA client: no secret
        authFlows: { userSrp: true },
      });
      for (const group of ['moderator', 'authorized_official']) {
        new cognito.CfnUserPoolGroup(this, `Group-${group}`, {
          userPoolId: this.userPool.userPoolId, groupName: group,
        });
      }
    }

    // ---------------------------------------------------------------- realtime (event API only here)
    if (config.realtime === 'appsync') {
      this.eventApi = new appsync.EventApi(this, 'EventApi', {
        apiName: `${config.prefix}-events`,
        authorizationConfig: {
          authProviders: [{ authorizationType: appsync.AppSyncAuthorizationType.IAM }],
          connectionAuthModeTypes: [appsync.AppSyncAuthorizationType.IAM],
          defaultPublishAuthModeTypes: [appsync.AppSyncAuthorizationType.IAM],
          defaultSubscribeAuthModeTypes: [appsync.AppSyncAuthorizationType.IAM],
        },
      });
      this.eventApi.addChannelNamespace('incidents');
    }

    // ---------------------------------------------------------------- shared function wiring
    const tablePrefix = `${config.prefix}-`;
    const baseEnv: Record<string, string> = {
      SAFEPATH_PROFILE: 'aws',
      SAFEPATH_TABLE_PREFIX: tablePrefix,
      SAFEPATH_REPOSITORY: 'dynamo',
      SAFEPATH_PUBLISHER: config.realtime === 'appsync' ? 'appsync' : 'apigw',
      SAFEPATH_AUTH: config.useCognito ? 'cognito' : 'demo',
      SAFEPATH_CORS_ORIGINS: config.frontendOrigins.join(','),
      // Secrets are referenced by ARN only; the value is read at runtime with secretsmanager:GetSecretValue.
      SAFEPATH_TOKEN_SECRET_ARN: tokenSecret.secretArn,
      SAFEPATH_ALIAS_SECRET_ARN: aliasSecret.secretArn,
    };
    if (this.userPool && this.userPoolClient) {
      baseEnv.SAFEPATH_COGNITO_USER_POOL_ID = this.userPool.userPoolId;
      baseEnv.SAFEPATH_COGNITO_CLIENT_ID = this.userPoolClient.userPoolClientId;
      baseEnv.SAFEPATH_COGNITO_GROUP_MODERATOR = 'moderator';
      baseEnv.SAFEPATH_COGNITO_GROUP_OFFICIAL = 'authorized_official';
    }
    if (this.eventApi) {
      baseEnv.SAFEPATH_APPSYNC_EVENTS_ENDPOINT = `https://${this.eventApi.httpDns}/event`;
      baseEnv.SAFEPATH_APPSYNC_NAMESPACE = 'incidents';
    }

    const mkLogGroup = (name: string) => new logs.LogGroup(this, `${name}Logs`, {
      logGroupName: `/aws/lambda/${config.prefix}-${name.toLowerCase()}`,
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const grantTables = (fn: lambda.Function, names: readonly string[]) => {
      for (const n of names) data.tables[n].grantReadWriteData(fn);
    };
    const reserved = ctx('safepath:reservedConcurrency');
    const reservedConcurrentExecutions = reserved !== undefined && Number(reserved) > 0 ? Number(reserved) : undefined;

    // ---------------------------------------------------------------- API function
    const apiEnv: Record<string, string> = {
      ...baseEnv,
      SAFEPATH_GRAPH_BUCKET: data.graphsBucket.bucketName,
      SAFEPATH_GRAPH_PREFIX: 'graphs',
      SAFEPATH_PHOTO_BUCKET: data.photosBucket.bucketName,
      SAFEPATH_BLOB_PREFIX: 'photos/',
      SAFEPATH_BLOBSTORE: 's3',
      SAFEPATH_LAMBDA_EAGER_INIT: '1',
    };
    this.apiFunction = new lambda.Function(this, 'ApiFunction', {
      functionName: `${config.prefix}-api`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'lambda_entry.handler',
      code,
      memorySize: API_MEMORY_MB,
      timeout: Duration.seconds(API_TIMEOUT_SECONDS),
      environment: apiEnv,
      logGroup: mkLogGroup('Api'),
      reservedConcurrentExecutions,
    });
    grantTables(this.apiFunction, Object.keys(data.tables));
    this.apiFunction.addToRolePolicy(new iam.PolicyStatement({
      sid: 'GraphsReadOnly',
      actions: ['s3:GetObject'],
      resources: [data.graphsBucket.arnForObjects('graphs/*')],
    }));
    this.apiFunction.addToRolePolicy(new iam.PolicyStatement({
      sid: 'GraphsList',
      actions: ['s3:ListBucket'],
      resources: [data.graphsBucket.bucketArn],
      conditions: { StringLike: { 's3:prefix': ['graphs/*', 'graphs'] } },
    }));
    this.apiFunction.addToRolePolicy(new iam.PolicyStatement({
      sid: 'PhotosPresign', // presigned URLs are signed with this role: PutObject/GetObject on photos/* only
      actions: ['s3:PutObject', 's3:GetObject'],
      resources: [data.photosBucket.arnForObjects('photos/*')],
    }));
    tokenSecret.grantRead(this.apiFunction);
    aliasSecret.grantRead(this.apiFunction);
    if (this.eventApi) this.eventApi.grantPublish(this.apiFunction);
    if (config.enableBedrock) {
      const modelId = String(ctx('safepath:bedrockModelId') ?? 'amazon.nova-lite-v1:0');
      this.apiFunction.addEnvironment('SAFEPATH_BEDROCK_MODEL_ID', modelId);
      this.apiFunction.addToRolePolicy(new iam.PolicyStatement({
        sid: 'BedrockInvokeOneModel',
        actions: ['bedrock:InvokeModel'],
        resources: [this.formatArn({
          service: 'bedrock', account: '', resource: 'foundation-model', resourceName: modelId,
        })],
      }));
    }

    // ---------------------------------------------------------------- HTTP API
    this.httpApi = new apigw.HttpApi(this, 'HttpApi', {
      apiName: `${config.prefix}-http`,
      createDefaultStage: false,
      corsPreflight: {
        allowOrigins: config.frontendOrigins, // explicit origins only, never '*'
        allowMethods: [apigw.CorsHttpMethod.GET, apigw.CorsHttpMethod.POST, apigw.CorsHttpMethod.PUT,
          apigw.CorsHttpMethod.PATCH, apigw.CorsHttpMethod.DELETE, apigw.CorsHttpMethod.OPTIONS],
        allowHeaders: ['Authorization', 'Content-Type', 'Idempotency-Key'],
        maxAge: Duration.minutes(10),
      },
    });
    new apigw.HttpRoute(this, 'ProxyRoute', {
      httpApi: this.httpApi,
      routeKey: apigw.HttpRouteKey.DEFAULT, // catch-all $default -> API Lambda
      integration: new HttpLambdaIntegration('ApiIntegration', this.apiFunction, {
        payloadFormatVersion: apigw.PayloadFormatVersion.VERSION_2_0, // Mangum
      }),
    });
    const httpStage = new apigw.HttpStage(this, 'DefaultStage', {
      httpApi: this.httpApi,
      stageName: '$default',
      autoDeploy: true,
      throttle: { rateLimit: THROTTLE_RATE, burstLimit: THROTTLE_BURST },
    });

    // ---------------------------------------------------------------- WebSocket fallback
    let wsUrl = 'n/a';
    let wsCallback = '';
    if (config.realtime === 'apigw-ws') {
      this.websocketFunction = new lambda.Function(this, 'WebSocketFunction', {
        functionName: `${config.prefix}-websocket`,
        runtime: lambda.Runtime.PYTHON_3_12,
        handler: 'lambda_entry.websocket_handler',
        code,
        memorySize: 512,
        timeout: Duration.seconds(10),
        environment: { ...baseEnv },
        logGroup: mkLogGroup('WebSocket'),
      });
      grantTables(this.websocketFunction, WEBSOCKET_TABLES);
      const integration = new WebSocketLambdaIntegration('WsIntegration', this.websocketFunction);
      this.webSocketApi = new apigw.WebSocketApi(this, 'WebSocketApi', {
        apiName: `${config.prefix}-ws`,
        connectRouteOptions: { integration },
        disconnectRouteOptions: { integration },
        defaultRouteOptions: { integration },
      });
      const wsStage = new apigw.WebSocketStage(this, 'WebSocketStage', {
        webSocketApi: this.webSocketApi,
        stageName: 'prod',
        autoDeploy: true,
        throttle: { rateLimit: THROTTLE_RATE, burstLimit: THROTTLE_BURST },
      });
      wsUrl = wsStage.url;
      wsCallback = wsStage.callbackUrl;
      const manageArn = this.formatArn({
        service: 'execute-api',
        resource: this.webSocketApi.apiId,
        resourceName: `${wsStage.stageName}/POST/@connections/*`,
      });
      const manage = new iam.PolicyStatement({
        sid: 'WebSocketManageConnections',
        actions: ['execute-api:ManageConnections'],
        resources: [manageArn],
      });
      for (const fn of [this.apiFunction]) {
        fn.addEnvironment('SAFEPATH_WS_ENDPOINT', wsStage.callbackUrl);
        fn.addToRolePolicy(manage);
      }
    }

    // ---------------------------------------------------------------- sweeper
    const sweeperEnv = { ...baseEnv };
    this.sweeperFunction = new lambda.Function(this, 'SweeperFunction', {
      functionName: `${config.prefix}-sweeper`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'lambda_entry.sweeper_handler',
      code,
      memorySize: 512,
      timeout: Duration.seconds(55),
      environment: sweeperEnv,
      logGroup: mkLogGroup('Sweeper'),
    });
    grantTables(this.sweeperFunction, SWEEPER_TABLES);
    if (this.eventApi) this.eventApi.grantPublish(this.sweeperFunction);
    if (this.webSocketApi) {
      this.sweeperFunction.addEnvironment('SAFEPATH_WS_ENDPOINT', wsCallback);
      this.sweeperFunction.addToRolePolicy(new iam.PolicyStatement({
        sid: 'WebSocketManageConnections',
        actions: ['execute-api:ManageConnections'],
        resources: [this.formatArn({
          service: 'execute-api',
          resource: this.webSocketApi.apiId,
          resourceName: 'prod/POST/@connections/*',
        })],
      }));
    }
    new events.Rule(this, 'SweeperSchedule', {
      ruleName: `${config.prefix}-sweeper-every-minute`,
      schedule: events.Schedule.rate(Duration.minutes(1)),
      targets: [new targets.LambdaFunction(this.sweeperFunction, { retryAttempts: 0 })],
    });

    // ---------------------------------------------------------------- alarms (no actions; visibility only)
    for (const [label, fn] of [['Api', this.apiFunction], ['Sweeper', this.sweeperFunction]] as const) {
      new cloudwatch.Alarm(this, `${label}ErrorsAlarm`, {
        metric: fn.metricErrors({ period: Duration.minutes(5) }),
        threshold: 5, evaluationPeriods: 1,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      });
    }
    new cloudwatch.Alarm(this, 'ApiThrottlesAlarm', {
      metric: this.apiFunction.metricThrottles({ period: Duration.minutes(5) }),
      threshold: 1, evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    // ---------------------------------------------------------------- outputs
    new CfnOutput(this, 'HttpApiUrl', { value: httpStage.url });
    new CfnOutput(this, 'WebSocketUrl', { value: wsUrl });
    new CfnOutput(this, 'ApiFunctionName', { value: this.apiFunction.functionName });
    new CfnOutput(this, 'SweeperFunctionName', { value: this.sweeperFunction.functionName });
    if (this.websocketFunction) new CfnOutput(this, 'WebSocketFunctionName', { value: this.websocketFunction.functionName });
    if (this.eventApi) new CfnOutput(this, 'AppSyncEventsEndpoint', { value: `https://${this.eventApi.httpDns}/event` });
    if (this.userPool && this.userPoolClient) {
      new CfnOutput(this, 'UserPoolId', { value: this.userPool.userPoolId });
      new CfnOutput(this, 'UserPoolClientId', { value: this.userPoolClient.userPoolClientId });
    }
    new CfnOutput(this, 'TokenSecretArn', { value: tokenSecret.secretArn });
  }
}
