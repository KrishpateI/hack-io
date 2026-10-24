import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { ApiStack } from '../lib/api-stack';
import { BudgetStack, budgetEnabled } from '../lib/budget-stack';
import { resolveConfig } from '../lib/config';
import { DataStack } from '../lib/data-stack';
import { ObservabilityStack } from '../lib/observability-stack';
import { applyProjectTags } from '../lib/tagging';
import { WebStack } from '../lib/web-stack';

function synth(context: Record<string, string> = {}) {
  const app = new App({ context: { '@aws-cdk/aws-iam:minimizePolicies': true, 'safepath:lambdaZip': '/nonexistent/x.zip', ...context } });
  const config = resolveConfig(app.node, '/nonexistent/discovery.json');
  const data = new DataStack(app, 'safepath-data', { config });
  const api = new ApiStack(app, 'safepath-api', { config, data });
  const web = new WebStack(app, 'safepath-web', { config, api });
  const obs = new ObservabilityStack(app, 'safepath-observability', { config, api, data });
  const budget = budgetEnabled(config) ? new BudgetStack(app, 'safepath-budget', { config }) : undefined;
  applyProjectTags(app, config.environment);
  return { web: Template.fromStack(web), obs: Template.fromStack(obs), budget: budget ? Template.fromStack(budget) : undefined };
}

const tagMap = (tags: { Key: string; Value: string }[] | undefined) => Object.fromEntries((tags ?? []).map((t) => [t.Key, t.Value]));

function noWildcardActions(t: Template) {
  for (const p of Object.values(t.findResources('AWS::IAM::Policy'))) {
    for (const s of p.Properties.PolicyDocument.Statement) {
      for (const a of ([] as string[]).concat(s.Action)) expect(a).not.toMatch(/\*/);
    }
  }
}

describe('WebStack defaults (CloudFront off)', () => {
  const { web } = synth();
  test('private tagged site bucket', () => {
    web.hasResourceProperties('AWS::S3::Bucket', {
      PublicAccessBlockConfiguration: {
        BlockPublicAcls: true, BlockPublicPolicy: true, IgnorePublicAcls: true, RestrictPublicBuckets: true,
      },
      BucketEncryption: Match.objectLike({}),
    });
    web.resourceCountIs('AWS::S3::Bucket', 1);
    const b = Object.values(web.findResources('AWS::S3::Bucket'))[0];
    expect(tagMap(b.Properties.Tags).Project).toBe('SafePath');
    web.hasResourceProperties('AWS::S3::BucketPolicy', {
      PolicyDocument: { Statement: Match.arrayWith([Match.objectLike({ Effect: 'Deny', Condition: { Bool: { 'aws:SecureTransport': 'false' } } })]) },
    });
  });
  test('no distribution, no public policy, no custom-resource lambda', () => {
    web.resourceCountIs('AWS::CloudFront::Distribution', 0);
    web.resourceCountIs('AWS::Lambda::Function', 0);
    const policies = JSON.stringify(web.findResources('AWS::S3::BucketPolicy'));
    expect(policies).not.toContain('"Effect":"Allow"'); // only the enforceSSL Deny statement
    expect(policies).not.toContain('s3:GetObject');
  });
  test('prints local-serving instructions', () => {
    web.hasOutput('HostingInstructions', {});
  });
  test('no IAM wildcard actions', () => noWildcardActions(web));
});

describe('WebStack with enableCloudFront=true', () => {
  const { web } = synth({ enableCloudFront: 'true' });
  const dist = Object.values(web.findResources('AWS::CloudFront::Distribution'))[0].Properties.DistributionConfig;

  test('OAC, https redirect, root object, HTTP/2+3', () => {
    web.resourceCountIs('AWS::CloudFront::OriginAccessControl', 1);
    web.hasResourceProperties('AWS::CloudFront::OriginAccessControl', {
      OriginAccessControlConfig: Match.objectLike({ OriginAccessControlOriginType: 's3', SigningBehavior: 'always' }),
    });
    expect(dist.DefaultRootObject).toBe('index.html');
    expect(dist.DefaultCacheBehavior.ViewerProtocolPolicy).toBe('redirect-to-https');
    for (const b of dist.CacheBehaviors) expect(['redirect-to-https', 'https-only']).toContain(b.ViewerProtocolPolicy);
  });

  test('min TLS 1.2 is set (applies to custom certificates) ', () => {
    const custom = synth({ enableCloudFront: 'true', 'safepath:domainName': 'x.example.com',
      'safepath:certificateArn': 'arn:aws:acm:us-east-1:111111111111:certificate/abc' });
    const d = Object.values(custom.web.findResources('AWS::CloudFront::Distribution'))[0].Properties.DistributionConfig;
    expect(d.ViewerCertificate.MinimumProtocolVersion).toBe('TLSv1.2_2021');
  });

  test('response headers policy with HSTS, nosniff, frame deny, referrer, CSP', () => {
    web.hasResourceProperties('AWS::CloudFront::ResponseHeadersPolicy', {
      ResponseHeadersPolicyConfig: {
        SecurityHeadersConfig: Match.objectLike({
          StrictTransportSecurity: Match.objectLike({ AccessControlMaxAgeSec: 31536000 }),
          ContentTypeOptions: { Override: true },
          FrameOptions: { FrameOption: 'DENY', Override: true },
          ReferrerPolicy: Match.objectLike({ ReferrerPolicy: 'strict-origin-when-cross-origin' }),
          ContentSecurityPolicy: Match.objectLike({}),
        }),
      },
    });
    const csp = JSON.stringify(Object.values(web.findResources('AWS::CloudFront::ResponseHeadersPolicy'))[0]);
    expect(csp).toContain("worker-src 'self' blob:");
    expect(csp).toContain('connect-src');
    expect(csp).toContain("object-src 'none'");
  });

  test('SPA fallback via viewer-request function, API errors are not rewritten', () => {
    web.resourceCountIs('AWS::CloudFront::Function', 1);
    expect(dist.DefaultCacheBehavior.FunctionAssociations[0].EventType).toBe('viewer-request');
    expect(dist.CustomErrorResponses).toBeUndefined();
  });

  test('tiles/*, assets/*, no-cache paths and /api/* behaviors', () => {
    const patterns = dist.CacheBehaviors.map((b: { PathPattern: string }) => b.PathPattern);
    expect(patterns).toEqual(expect.arrayContaining(['tiles/*', 'assets/*', 'layers/*', 'index.html', 'sw.js', 'api/*']));
    const tiles = dist.CacheBehaviors.find((b: { PathPattern: string }) => b.PathPattern === 'tiles/*');
    expect(tiles.Compress).toBe(false); // byte offsets must stay valid for Range reads
    expect(tiles.AllowedMethods).toEqual(['GET', 'HEAD', 'OPTIONS']);
    const idx = dist.CacheBehaviors.find((b: { PathPattern: string }) => b.PathPattern === 'index.html');
    expect(idx.CachePolicyId).toBe('4135ea2d-6df8-44a3-9df3-4b5a84be39ad'); // CachingDisabled
    const apiB = dist.CacheBehaviors.find((b: { PathPattern: string }) => b.PathPattern === 'api/*');
    expect(apiB.ViewerProtocolPolicy).toBe('https-only');
    expect(apiB.AllowedMethods).toHaveLength(7);
    web.hasResourceProperties('AWS::CloudFront::CachePolicy', {
      CachePolicyConfig: Match.objectLike({ DefaultTTL: 31536000 }),
    });
  });

  test('bucket policy only for the CloudFront service principal, site bucket private, tagged', () => {
    web.hasResourceProperties('AWS::S3::BucketPolicy', {
      PolicyDocument: { Statement: Match.arrayWith([Match.objectLike({
        Principal: { Service: 'cloudfront.amazonaws.com' }, Action: 's3:GetObject',
      })]) },
    });
    web.resourceCountIs('AWS::S3::Bucket', 1);
    const d = Object.values(web.findResources('AWS::CloudFront::Distribution'))[0];
    expect(tagMap(d.Properties.Tags).Project).toBe('SafePath');
    noWildcardActions(web);
  });
});

describe('WebStack s3-website opt-in', () => {
  test('creates one clearly separate public bucket, only when explicit and CloudFront is off', () => {
    const { web } = synth({ 'safepath:webHosting': 's3-website' });
    web.resourceCountIs('AWS::S3::Bucket', 2);
    const buckets = Object.values(web.findResources('AWS::S3::Bucket'));
    const pub = buckets.find((b) => b.Properties.WebsiteConfiguration);
    expect(pub).toBeDefined();
    expect(tagMap(pub!.Properties.Tags).Project).toBe('SafePath');
    web.hasOutput('PublicWebsiteUrl', {});
  });
  test('ignored when CloudFront is enabled', () => {
    const { web } = synth({ 'safepath:webHosting': 's3-website', enableCloudFront: 'true' });
    web.resourceCountIs('AWS::S3::Bucket', 1);
  });
});

describe('ObservabilityStack', () => {
  test('dashboard and alarms without email: no topic', () => {
    const { obs } = synth();
    obs.resourceCountIs('AWS::CloudWatch::Dashboard', 1);
    obs.resourceCountIs('AWS::SNS::Topic', 0);
    obs.resourceCountIs('AWS::SNS::Subscription', 0);
    expect(Object.keys(obs.findResources('AWS::CloudWatch::Alarm')).length).toBeGreaterThanOrEqual(5);
    const body = JSON.stringify(obs.findResources('AWS::CloudWatch::Dashboard'));
    for (const m of ['Latency', '5xx', 'Duration', 'ReadThrottleEvents', 'ConsumedWriteCapacityUnits', 'Invocations']) {
      expect(body).toContain(m);
    }
  });
  test('email creates topic, subscription and alarm actions', () => {
    const { obs } = synth({ 'safepath:alertEmail': 'test@example.com' });
    obs.resourceCountIs('AWS::SNS::Topic', 1);
    obs.hasResourceProperties('AWS::SNS::Subscription', { Protocol: 'email', Endpoint: 'test@example.com' });
    obs.hasResourceProperties('AWS::CloudWatch::Alarm', { AlarmActions: Match.anyValue() });
    const topic = Object.values(obs.findResources('AWS::SNS::Topic'))[0];
    expect(tagMap(topic.Properties.Tags).Project).toBe('SafePath');
    noWildcardActions(obs);
  });
  test('invalid email is rejected', () => {
    expect(() => synth({ 'safepath:alertEmail': 'not-an-email' })).toThrow(/alertEmail/);
  });
});

describe('BudgetStack', () => {
  test('no budget stack by default, with only one of the two inputs, or with email but no flag', () => {
    expect(synth().budget).toBeUndefined();
    expect(synth({ enableBudget: 'true' }).budget).toBeUndefined();
    expect(synth({ 'safepath:alertEmail': 'a@b.co' }).budget).toBeUndefined();
  });
  test('enabled with email: 25 USD monthly, ACTUAL 80% + FORECASTED 100%', () => {
    const budget = synth({ enableBudget: 'true', 'safepath:alertEmail': 'test@example.com' }).budget!;
    budget.hasResourceProperties('AWS::Budgets::Budget', {
      Budget: Match.objectLike({ BudgetType: 'COST', TimeUnit: 'MONTHLY', BudgetLimit: { Amount: 25, Unit: 'USD' } }),
      NotificationsWithSubscribers: [
        Match.objectLike({
          Notification: Match.objectLike({ NotificationType: 'ACTUAL', Threshold: 80, ThresholdType: 'PERCENTAGE' }),
          Subscribers: [{ SubscriptionType: 'EMAIL', Address: 'test@example.com' }],
        }),
        Match.objectLike({ Notification: Match.objectLike({ NotificationType: 'FORECASTED', Threshold: 100 }) }),
      ],
    });
  });
  test('budget is tagged Project=SafePath', () => {
    const budget = synth({ enableBudget: 'true', 'safepath:alertEmail': 'a@b.co' }).budget!;
    budget.hasResourceProperties('AWS::Budgets::Budget', {
      ResourceTags: Match.arrayWith([{ Key: 'Project', Value: 'SafePath' }]),
    });
  });
  test('budgetUsd override', () => {
    const budget = synth({ enableBudget: 'true', 'safepath:alertEmail': 'a@b.co', 'safepath:budgetUsd': '40' }).budget!;
    budget.hasResourceProperties('AWS::Budgets::Budget', { Budget: Match.objectLike({ BudgetLimit: { Amount: 40 } }) });
  });
});