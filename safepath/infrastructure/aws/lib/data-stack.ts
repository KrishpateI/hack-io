import { Aws, CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import { SafePathConfig } from './config';

interface TableSpec {
  /** Logical name; must match backend/app/adapters/aws/dynamo.py TABLES. */
  readonly name: string;
  /** TTL on the `ttl` epoch-seconds attribute (dynamo.py TTL_ATTRIBUTE). */
  readonly ttl?: boolean;
  /** Point-in-time recovery: only for durable, hard-to-rebuild data. */
  readonly pitr?: boolean;
  /** GSIs, mirroring dynamo.py INDEXES (ALL projection, string partition key). */
  readonly gsis?: { name: string; partitionKey: string }[];
}

/** Keep in sync with backend/app/adapters/aws/dynamo.py (TABLES, INDEXES). */
export const TABLE_SPECS: readonly TableSpec[] = [
  { name: 'users', pitr: true },
  { name: 'credentials' },
  { name: 'incidents' },
  { name: 'notices' },
  { name: 'overlays' },
  { name: 'overlay_meta' },
  { name: 'reports', gsis: [{ name: 'report-id', partitionKey: 'report_id' }] },
  { name: 'report_votes' },
  { name: 'messages' },
  { name: 'message_index' },
  { name: 'checkins', gsis: [{ name: 'user-index', partitionKey: 'user_id' }] },
  { name: 'checkin_locations' },
  { name: 'events', ttl: true },
  { name: 'audit', pitr: true },
  { name: 'ratelimits', ttl: true },
  { name: 'demo_scenarios' },
  { name: 'connections', ttl: true },
];

export interface DataStackProps extends StackProps {
  readonly config: SafePathConfig;
}

/**
 * Data layer only: DynamoDB tables and S3 buckets. No IAM here; Lambda roles (task 12.4) get
 * least-privilege grants on these resources.
 *
 * RemovalPolicy.DESTROY is deliberate: this is a tagged sandbox (Project=SafePath, Simulated=true)
 * so `cdk destroy` leaves nothing billable behind. Do not reuse this stack for real data.
 * Buckets are NOT auto-emptied (autoDeleteObjects=false, avoids a custom-resource Lambda with
 * broad S3 rights); empty them manually before `cdk destroy` (see README).
 */
export class DataStack extends Stack {
  public readonly tables: Record<string, dynamodb.Table> = {};
  public readonly graphsBucket: s3.Bucket;
  public readonly photosBucket: s3.Bucket;

  constructor(scope: Construct, id: string, props: DataStackProps) {
    super(scope, id, props);
    const { config } = props;

    for (const spec of TABLE_SPECS) {
      const table = new dynamodb.Table(this, `Table-${spec.name}`, {
        tableName: `${config.prefix}-${spec.name}`, // backend: SAFEPATH_TABLE_PREFIX="<prefix>-"
        partitionKey: { name: 'pk', type: dynamodb.AttributeType.STRING },
        sortKey: { name: 'sk', type: dynamodb.AttributeType.STRING },
        billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
        encryption: dynamodb.TableEncryption.AWS_MANAGED,
        pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: !!spec.pitr },
        timeToLiveAttribute: spec.ttl ? 'ttl' : undefined,
        removalPolicy: RemovalPolicy.DESTROY,
      });
      for (const g of spec.gsis ?? []) {
        table.addGlobalSecondaryIndex({
          indexName: g.name,
          partitionKey: { name: g.partitionKey, type: dynamodb.AttributeType.STRING },
          projectionType: dynamodb.ProjectionType.ALL,
        });
      }
      this.tables[spec.name] = table;
      new CfnOutput(this, `TableName-${spec.name}`, { value: table.tableName });
    }

    const suffix = `${Aws.ACCOUNT_ID}-${Aws.REGION}`; // tokens: no hard-coded account ids
    const common: s3.BucketProps = {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: false,
    };

    // Graph artifacts (graphs/<version>/...). Versioned; old noncurrent versions expire.
    this.graphsBucket = new s3.Bucket(this, 'GraphsBucket', {
      ...common,
      bucketName: `${config.prefix}-graphs-${suffix}`,
      versioned: true,
      lifecycleRules: [{
        id: 'expire-noncurrent-graph-versions',
        noncurrentVersionExpiration: Duration.days(90),
        abortIncompleteMultipartUploadAfter: Duration.days(1),
      }],
    });

    // Photos (photos/...), reached only through presigned URLs; expire after N days.
    this.photosBucket = new s3.Bucket(this, 'PhotosBucket', {
      ...common,
      bucketName: `${config.prefix}-photos-${suffix}`,
      lifecycleRules: [{
        id: 'expire-photos',
        prefix: 'photos/',
        expiration: Duration.days(config.photoRetentionDays),
        abortIncompleteMultipartUploadAfter: Duration.days(1),
      }],
      cors: [{
        allowedMethods: [s3.HttpMethods.PUT, s3.HttpMethods.GET, s3.HttpMethods.HEAD],
        allowedOrigins: config.frontendOrigins, // explicit origins only, never '*'
        allowedHeaders: ['Content-Type', 'Content-Length', 'x-amz-*'],
        exposedHeaders: ['ETag'],
        maxAge: 600,
      }],
    });

    new CfnOutput(this, 'GraphsBucketName', { value: this.graphsBucket.bucketName });
    new CfnOutput(this, 'PhotosBucketName', { value: this.photosBucket.bucketName });
    new CfnOutput(this, 'TablePrefix', { value: `${config.prefix}-` });
  }
}
