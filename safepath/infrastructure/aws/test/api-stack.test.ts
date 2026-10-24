import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { ApiStack } from '../lib/api-stack';
import { resolveConfig } from '../lib/config';
import { DataStack } from '../lib/data-stack';
import { applyProjectTags } from '../lib/tagging';

function synth(context: Record<string, string> = {}) {
  const app = new App({ context: { '@aws-cdk/aws-iam:minimizePolicies': true, 'safepath:lambdaZip': '/nonexistent/safepath-lambda.zip', ...context } });
  const config = resolveConfig(app.node, '/nonexistent/discovery.json');
  const data = new DataStack(app, 'safepath-data', { config });
  const stack = new ApiStack(app, 'safepath-api', { config, data });
  applyProjectTags(app, config.environment);
  return Template.fromStack(stack);
}

const asArray = <T>(v: T | T[]): T[] => (Array.isArray(v) ? v : [v]);

describe('ApiStack (defaults)', () => {
  const t = synth();
  const fns = Object.values(t.findResources('AWS::Lambda::Function'));

  test('three... api, sweeper, websocket functions on python3.12', () => {
    expect(fns.length).toBe(3);
    for (const f of fns) expect(f.Properties.Runtime).toBe('python3.12');
    const handlers = fns.map((f) => f.Properties.Handler).sort();
    expect(handlers).toEqual(['lambda_entry.handler', 'lambda_entry.sweeper_handler', 'lambda_entry.websocket_handler']);
  });

  test('API function: 3008 MB, 29 s, aws profile, no plain secret', () => {
    t.hasResourceProperties('AWS::Lambda::Function', {
      Handler: 'lambda_entry.handler', MemorySize: 3008, Timeout: 29,
      Environment: { Variables: Match.objectLike({ SAFEPATH_PROFILE: 'aws', SAFEPATH_TABLE_PREFIX: 'safepath-' }) },
    });
    for (const f of fns) {
      const vars = f.Properties.Environment.Variables as Record<string, unknown>;
      expect(vars).not.toHaveProperty('SAFEPATH_TOKEN_SECRET');
      expect(vars).not.toHaveProperty('SAFEPATH_ALIAS_SECRET');
      expect(JSON.stringify(vars)).not.toMatch(/resolve:secretsmanager/);
    }
    t.resourceCountIs('AWS::SecretsManager::Secret', 2);
  });

  test('log retention is 7 days', () => {
    const groups = Object.values(t.findResources('AWS::Logs::LogGroup'));
    expect(groups.length).toBe(3);
    for (const g of groups) expect(g.Properties.RetentionInDays).toBe(7);
  });

  test('HTTP API: payload 2.0 catch-all, throttling 50/100, CORS not *', () => {
    t.hasResourceProperties('AWS::ApiGatewayV2::Integration', { PayloadFormatVersion: '2.0', IntegrationType: 'AWS_PROXY' });
    t.hasResourceProperties('AWS::ApiGatewayV2::Route', { RouteKey: '$default' });
    t.hasResourceProperties('AWS::ApiGatewayV2::Stage', {
      DefaultRouteSettings: { ThrottlingRateLimit: 50, ThrottlingBurstLimit: 100 },
    });
    const api = Object.values(t.findResources('AWS::ApiGatewayV2::Api'))
      .find((a) => a.Properties.ProtocolType === 'HTTP')!;
    const origins = api.Properties.CorsConfiguration.AllowOrigins as string[];
    expect(origins.length).toBeGreaterThan(0);
    expect(origins).not.toContain('*');
  });

  test('sweeper runs every minute', () => {
    t.hasResourceProperties('AWS::Events::Rule', { ScheduleExpression: 'rate(1 minute)' });
  });

  test('websocket API with the three routes (default realtime=apigw-ws)', () => {
    t.hasResourceProperties('AWS::ApiGatewayV2::Api', { ProtocolType: 'WEBSOCKET' });
    const keys = Object.values(t.findResources('AWS::ApiGatewayV2::Route'))
      .map((r) => r.Properties.RouteKey);
    expect(keys).toEqual(expect.arrayContaining(['$connect', '$disconnect', '$default']));
    expect(Object.keys(t.findResources('AWS::AppSync::Api')).length).toBe(0);
  });

  test('no Cognito by default', () => {
    t.resourceCountIs('AWS::Cognito::UserPool', 0);
    t.resourceCountIs('AWS::Cognito::UserPoolClient', 0);
  });

  test('IAM: no wildcard action or resource, no dynamodb:*, no managed admin', () => {
    const statements: { Action: string | string[]; Resource: unknown; Effect: string }[] = [];
    for (const p of Object.values(t.findResources('AWS::IAM::Policy'))) {
      statements.push(...asArray(p.Properties.PolicyDocument.Statement));
    }
    expect(statements.length).toBeGreaterThan(0);
    for (const s of statements) {
      for (const a of asArray(s.Action)) {
        expect(a).not.toBe('*');
        expect(a).not.toMatch(/:\*$/);
      }
      for (const r of asArray(s.Resource as string | string[])) expect(r).not.toBe('*');
    }
    for (const role of Object.values(t.findResources('AWS::IAM::Role'))) {
      const arns = JSON.stringify(role.Properties.ManagedPolicyArns ?? []);
      expect(arns).not.toMatch(/AdministratorAccess|PowerUser/);
    }
  });

  test('S3 grants are prefix-limited and websocket permission is API-specific', () => {
    const text = JSON.stringify(t.findResources('AWS::IAM::Policy'));
    expect(text).toContain('graphs/*');
    expect(text).toContain('photos/*');
    expect(text).toContain('execute-api:ManageConnections');
    expect(text).toContain('@connections/*');
  });

  test('resources are tagged Project=SafePath', () => {
    const tagged = [
      ...Object.values(t.findResources('AWS::Lambda::Function')),
      ...Object.values(t.findResources('AWS::Logs::LogGroup')),
      ...Object.values(t.findResources('AWS::SecretsManager::Secret')),
      ...Object.values(t.findResources('AWS::ApiGatewayV2::Api')),
    ];
    expect(tagged.length).toBeGreaterThan(0);
    for (const r of tagged) {
      const tags = r.Properties.Tags;
      const list: { Key: string; Value: string }[] = Array.isArray(tags)
        ? tags : Object.entries(tags ?? {}).map(([Key, Value]) => ({ Key, Value: String(Value) }));
      expect(list).toEqual(expect.arrayContaining([{ Key: 'Project', Value: 'SafePath' }]));
    }
  });
});

describe('ApiStack (flags)', () => {
  test('useCognito=true creates pool, secretless client and both groups', () => {
    const t = synth({ useCognito: 'true' });
    t.resourceCountIs('AWS::Cognito::UserPool', 1);
    t.hasResourceProperties('AWS::Cognito::UserPoolClient', { GenerateSecret: false });
    t.hasResourceProperties('AWS::Cognito::UserPoolGroup', { GroupName: 'moderator' });
    t.hasResourceProperties('AWS::Cognito::UserPoolGroup', { GroupName: 'authorized_official' });
    t.hasResourceProperties('AWS::Lambda::Function', {
      Handler: 'lambda_entry.handler',
      Environment: { Variables: Match.objectLike({ SAFEPATH_AUTH: 'cognito', SAFEPATH_COGNITO_USER_POOL_ID: Match.anyValue() }) },
    });
  });

  test('realtime=appsync creates an Events API and no websocket API', () => {
    const t = synth({ realtime: 'appsync' });
    t.resourceCountIs('AWS::AppSync::Api', 1);
    t.resourceCountIs('AWS::Lambda::Function', 2);
    const protocols = Object.values(t.findResources('AWS::ApiGatewayV2::Api')).map((a) => a.Properties.ProtocolType);
    expect(protocols).toEqual(['HTTP']);
  });

  test('enableBedrock adds a single-model invoke permission', () => {
    const t = synth({ enableBedrock: 'true' });
    t.hasResourceProperties('AWS::IAM::Policy', {
      PolicyDocument: { Statement: Match.arrayWith([Match.objectLike({ Action: 'bedrock:InvokeModel' })]) },
    });
  });

  test('requireLambdaPackage fails clearly when the zip is missing', () => {
    expect(() => synth({ 'safepath:requireLambdaPackage': 'true' })).toThrow(/package_lambda/);
  });
});
