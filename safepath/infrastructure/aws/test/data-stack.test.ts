import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { resolveConfig } from '../lib/config';
import { DataStack, TABLE_SPECS } from '../lib/data-stack';
import { applyProjectTags } from '../lib/tagging';

function synth(context: Record<string, string> = {}) {
  const app = new App({ context });
  const config = resolveConfig(app.node, '/nonexistent/discovery.json');
  const stack = new DataStack(app, 'safepath-data', { config });
  applyProjectTags(app, config.environment);
  return { template: Template.fromStack(stack), config };
}

describe('DataStack', () => {
  const { template, config } = synth();
  const tables = Object.values(template.findResources('AWS::DynamoDB::Table'));
  const buckets = Object.values(template.findResources('AWS::S3::Bucket'));

  test('every taggable resource is tagged Project=SafePath, Simulated=true', () => {
    expect(tables.length).toBe(TABLE_SPECS.length);
    expect(buckets.length).toBe(2);
    for (const r of [...tables, ...buckets]) {
      const tags = r.Properties.Tags as { Key: string; Value: string }[];
      expect(tags).toEqual(expect.arrayContaining([
        { Key: 'Project', Value: 'SafePath' },
        { Key: 'Simulated', Value: 'true' },
        { Key: 'Environment', Value: 'sandbox' },
      ]));
    }
  });

  test('tables are on-demand with SSE and names match backend', () => {
    for (const t of tables) {
      expect(t.Properties.BillingMode).toBe('PAY_PER_REQUEST');
      expect(t.Properties.KeySchema).toEqual([
        { AttributeName: 'pk', KeyType: 'HASH' }, { AttributeName: 'sk', KeyType: 'RANGE' }]);
      expect(t.Properties.SSESpecification).toEqual({ SSEEnabled: true });
    }
    const names = tables.map((t) => t.Properties.TableName).sort();
    expect(names).toEqual(TABLE_SPECS.map((s) => `${config.prefix}-${s.name}`).sort());
  });

  test('GSIs report-id and user-index exist', () => {
    template.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: 'safepath-reports',
      GlobalSecondaryIndexes: [Match.objectLike({ IndexName: 'report-id' })],
    });
    template.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: 'safepath-checkins',
      GlobalSecondaryIndexes: [Match.objectLike({ IndexName: 'user-index' })],
    });
  });

  test('TTL on events and ratelimits; PITR only on users and audit', () => {
    for (const n of ['events', 'ratelimits']) {
      template.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: `safepath-${n}`,
        TimeToLiveSpecification: { AttributeName: 'ttl', Enabled: true },
      });
    }
    for (const t of tables) {
      const pitr = t.Properties.PointInTimeRecoverySpecification?.PointInTimeRecoveryEnabled === true;
      expect(pitr).toBe(['safepath-users', 'safepath-audit'].includes(t.Properties.TableName));
    }
  });

  test('buckets block public access, encrypt, and enforce SSL', () => {
    for (const b of buckets) {
      expect(b.Properties.PublicAccessBlockConfiguration).toEqual({
        BlockPublicAcls: true, BlockPublicPolicy: true, IgnorePublicAcls: true, RestrictPublicBuckets: true });
      expect(b.Properties.BucketEncryption.ServerSideEncryptionConfiguration[0]
        .ServerSideEncryptionByDefault.SSEAlgorithm).toBe('AES256');
    }
    const policies = Object.values(template.findResources('AWS::S3::BucketPolicy'));
    expect(policies.length).toBe(2);
    for (const p of policies) {
      expect(JSON.stringify(p.Properties.PolicyDocument)).toContain('"aws:SecureTransport":"false"');
    }
  });

  test('graphs bucket versioned; photos expire; CORS limited to origins', () => {
    template.hasResourceProperties('AWS::S3::Bucket', {
      VersioningConfiguration: { Status: 'Enabled' },
    });
    template.hasResourceProperties('AWS::S3::Bucket', {
      LifecycleConfiguration: { Rules: Match.arrayWith([Match.objectLike({ ExpirationInDays: 30, Prefix: 'photos/' })]) },
      CorsConfiguration: { CorsRules: [Match.objectLike({
        AllowedOrigins: ['http://localhost:5173', 'http://127.0.0.1:5173'] })] },
    });
    const custom = synth({ 'safepath:frontendOrigins': 'https://app.example.org', 'safepath:photoRetentionDays': '7' });
    custom.template.hasResourceProperties('AWS::S3::Bucket', {
      CorsConfiguration: { CorsRules: [Match.objectLike({ AllowedOrigins: ['https://app.example.org'] })] },
      LifecycleConfiguration: { Rules: Match.arrayWith([Match.objectLike({ ExpirationInDays: 7 })]) },
    });
  });

  test('no IAM policies with wildcard actions or resources, no hard-coded account id', () => {
    const json = JSON.stringify(template.toJSON());
    expect(json).not.toMatch(/\d{12}/);
    for (const r of Object.values(template.findResources('AWS::IAM::Policy'))) {
      expect(JSON.stringify(r)).not.toMatch(/"Action":"\*"|"Resource":"\*"/);
    }
    // Bucket policies may only use the s3:* deny for insecure transport.
    for (const p of Object.values(template.findResources('AWS::S3::BucketPolicy'))) {
      for (const st of p.Properties.PolicyDocument.Statement) {
        expect(st.Effect).toBe('Deny');
      }
    }
    expect(template.findResources('AWS::IAM::Role')).toEqual({});
  });

  test('outputs table and bucket names', () => {
    const out = Object.keys(template.toJSON().Outputs);
    expect(out).toEqual(expect.arrayContaining(['GraphsBucketName', 'PhotosBucketName', 'TablePrefix']));
  });
});

describe('config flags', () => {
  test('conservative defaults', () => {
    const { config } = synth();
    expect(config).toMatchObject({ useCognito: false, enableBedrock: false, enableCloudFront: false,
      enableBudget: false, realtime: 'apigw-ws' });
  });
  test('-c overrides', () => {
    const { config } = synth({ useCognito: 'true', realtime: 'appsync' });
    expect(config.useCognito).toBe(true);
    expect(config.realtime).toBe('appsync');
  });
});
