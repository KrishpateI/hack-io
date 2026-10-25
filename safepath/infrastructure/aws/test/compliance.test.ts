/**
 * Cross-stack CDK assertion tests (task 12.7): synthesizes the whole app offline, the same way bin/app.ts
 * wires it, and checks the account-wide guarantees that per-stack tests cannot see on their own.
 *
 * Requirements: 29.6 (tagging), 31.1 (per-component IAM), 31.5 (TLS + encryption at rest),
 * 32.1 (serverless, no EC2), 32.2 (on-demand DynamoDB), 32.5 (throttling 50/100), 32.6 (7-day logs).
 */
import { App, CfnResource, Stack, TagManager } from 'aws-cdk-lib';
import { Annotations, Match, Template } from 'aws-cdk-lib/assertions';
import { ApiStack, SWEEPER_TABLES, WEBSOCKET_TABLES } from '../lib/api-stack';
import { BudgetStack, budgetEnabled } from '../lib/budget-stack';
import { resolveConfig } from '../lib/config';
import { DataStack, TABLE_SPECS } from '../lib/data-stack';
import { ObservabilityStack } from '../lib/observability-stack';
import { applyProjectTags } from '../lib/tagging';
import { WebStack } from '../lib/web-stack';

interface Synthesized {
  app: App;
  stacks: Stack[];
  templates: Map<Stack, Template>;
  api: ApiStack;
  web: WebStack;
}

/** Mirrors bin/app.ts (all optional stacks included) without discovery.json, credentials or lookups. */
function synthApp(context: Record<string, string> = {}): Synthesized {
  const app = new App({ context: { 'safepath:lambdaZip': '/nonexistent/safepath-lambda.zip', ...context } });
  const config = resolveConfig(app.node, '/nonexistent/discovery.json');
  const data = new DataStack(app, 'safepath-data', { config });
  const api = new ApiStack(app, 'safepath-api', { config, data });
  const web = new WebStack(app, 'safepath-web', { config, api });
  const obs = new ObservabilityStack(app, 'safepath-observability', { config, api, data });
  const stacks: Stack[] = [data, api, web, obs];
  if (budgetEnabled(config)) stacks.push(new BudgetStack(app, 'safepath-budget', { config }));
  applyProjectTags(app, config.environment);
  const templates = new Map(stacks.map((s) => [s, Template.fromStack(s)] as const));
  return { app, stacks, templates, api, web };
}

const SCENARIOS: Record<string, Record<string, string>> = {
  defaults: {},
  'all features on': {
    useCognito: 'true', realtime: 'appsync', enableBedrock: 'true', enableCloudFront: 'true',
    enableBudget: 'true', 'safepath:alertEmail': 'alerts@example.com',
    'safepath:domainName': 'safepath.example.com',
    'safepath:certificateArn': 'arn:aws:acm:us-east-1:111111111111:certificate/abc',
  },
  'apigw-ws + cloudfront default cert': { enableCloudFront: 'true', 'safepath:alertEmail': 'alerts@example.com' },
};

type Res = { Type: string; Properties?: Record<string, any> };
const allResources = (s: Synthesized): [string, Res][] =>
  [...s.templates.values()].flatMap((t) => Object.entries(t.toJSON().Resources ?? {}) as [string, Res][]);
const asArray = <T>(v: T | T[] | undefined): T[] => (v === undefined ? [] : Array.isArray(v) ? v : [v]);

/** Normalizes the three CloudFormation tag shapes (list, map, Budgets ResourceTags) to a map. */
function tagsOf(r: Res): Record<string, string> {
  const p = r.Properties ?? {};
  const raw = p.Tags ?? p.ResourceTags;
  if (Array.isArray(raw)) return Object.fromEntries(raw.map((t: any) => [t.Key, t.Value]));
  if (raw && typeof raw === 'object') return Object.fromEntries(Object.entries(raw).map(([k, v]) => [k, String(v)]));
  return {};
}

for (const [name, ctx] of Object.entries(SCENARIOS)) {
  describe(`whole app: ${name}`, () => {
    const s = synthApp(ctx);
    const resources = allResources(s);
    const ofType = (type: string) => resources.filter(([, r]) => r.Type === type);

    // ------------------------------------------------------------------ 29.6 tagging
    test('every taggable resource in every stack carries Project=SafePath', () => {
      let checked = 0;
      for (const stack of s.stacks) {
        const tpl = s.templates.get(stack)!.toJSON().Resources;
        for (const c of stack.node.findAll()) {
          if (!CfnResource.isCfnResource(c) || !TagManager.isTaggable(c)) continue;
          const logicalId = stack.resolve(c.logicalId) as string;
          expect({ logicalId, project: tagsOf(tpl[logicalId]).Project }).toEqual({ logicalId, project: 'SafePath' });
          checked++;
        }
      }
      expect(checked).toBeGreaterThan(20);
      // Budgets are not ITaggable and are tagged explicitly.
      for (const [id, b] of ofType('AWS::Budgets::Budget')) expect({ id, ...tagsOf(b) }).toMatchObject({ id, Project: 'SafePath' });
    });

    // ------------------------------------------------------------------ 32.1 / 32.2 serverless, on-demand
    test('no EC2, VPC, NAT, RDS, caches, containers or other always-on compute', () => {
      const forbidden = /^AWS::(EC2|AutoScaling|RDS|ElastiCache|ECS|EKS|Redshift|OpenSearchService|Elasticsearch|DocDB|Neptune|MSK|DAX|ElasticBeanstalk|Lightsail|EMR|SageMaker)::/;
      expect(resources.filter(([, r]) => forbidden.test(r.Type)).map(([id, r]) => `${id}:${r.Type}`)).toEqual([]);
      for (const [id, f] of ofType('AWS::Lambda::Function')) {
        expect({ id, vpc: f.Properties?.VpcConfig }).toEqual({ id, vpc: undefined }); // a VPC Lambda would need NAT
      }
      expect(ofType('AWS::Lambda::EventSourceMapping')).toEqual([]);
      expect(resources.filter(([, r]) => /ProvisionedConcurrency/.test(JSON.stringify(r.Properties ?? {})))).toEqual([]);
    });

    test('all DynamoDB tables (and GSIs) are PAY_PER_REQUEST with no provisioned throughput', () => {
      const tables = ofType('AWS::DynamoDB::Table');
      expect(tables.length).toBe(TABLE_SPECS.length);
      expect(ofType('AWS::DynamoDB::GlobalTable')).toEqual([]);
      for (const [id, t] of tables) {
        expect({ id, mode: t.Properties!.BillingMode }).toEqual({ id, mode: 'PAY_PER_REQUEST' });
        expect(JSON.stringify(t.Properties)).not.toContain('ProvisionedThroughput');
      }
    });

    // ------------------------------------------------------------------ 32.6 logs
    test('every log group keeps logs exactly 7 days and every Lambda logs to one of them', () => {
      const groups = ofType('AWS::Logs::LogGroup');
      expect(groups.length).toBeGreaterThan(0);
      for (const [id, g] of groups) expect({ id, days: g.Properties!.RetentionInDays }).toEqual({ id, days: 7 });
      const groupIds = new Set(groups.map(([id]) => id));
      for (const [id, f] of ofType('AWS::Lambda::Function')) {
        // Without an explicit group, Lambda creates /aws/lambda/<name> with never-expire retention.
        const ref = f.Properties!.LoggingConfig?.LogGroup?.Ref;
        expect({ id, explicitGroup: groupIds.has(ref) }).toEqual({ id, explicitGroup: true });
      }
      expect(ofType('Custom::LogRetention')).toEqual([]);
    });

    // ------------------------------------------------------------------ 32.5 throttling
    test('every API Gateway stage throttles at 50 rps steady-state and 100 burst', () => {
      const stages = ofType('AWS::ApiGatewayV2::Stage');
      expect(stages.length).toBe(ctx.realtime === 'appsync' ? 1 : 2); // HTTP (+ WebSocket fallback)
      for (const [id, st] of stages) {
        expect({ id, ...st.Properties!.DefaultRouteSettings })
          .toMatchObject({ id, ThrottlingRateLimit: 50, ThrottlingBurstLimit: 100 });
        for (const rs of Object.values(st.Properties!.RouteSettings ?? {}) as any[]) {
          if (rs.ThrottlingRateLimit !== undefined) expect(rs.ThrottlingRateLimit).toBeLessThanOrEqual(50);
          if (rs.ThrottlingBurstLimit !== undefined) expect(rs.ThrottlingBurstLimit).toBeLessThanOrEqual(100);
        }
      }
      expect(ofType('AWS::ApiGateway::RestApi')).toEqual([]); // no unthrottled v1 API sneaks in
    });

    // ------------------------------------------------------------------ 31.5 S3 + encryption at rest
    test('every bucket is encrypted, private and TLS-only (no public bucket unless explicitly opted in)', () => {
      const buckets = ofType('AWS::S3::Bucket');
      expect(buckets.length).toBe(3); // graphs, photos, site
      for (const [id, b] of buckets) {
        expect({ id, pab: b.Properties!.PublicAccessBlockConfiguration }).toEqual({ id, pab: {
          BlockPublicAcls: true, BlockPublicPolicy: true, IgnorePublicAcls: true, RestrictPublicBuckets: true } });
        const sse = b.Properties!.BucketEncryption?.ServerSideEncryptionConfiguration?.[0]?.ServerSideEncryptionByDefault?.SSEAlgorithm;
        expect({ id, sse }).toEqual({ id, sse: expect.stringMatching(/^(AES256|aws:kms)$/) });
        expect(b.Properties!.WebsiteConfiguration).toBeUndefined();
        expect(JSON.stringify(b.Properties)).not.toMatch(/PublicRead/);
      }
      const policies = ofType('AWS::S3::BucketPolicy');
      expect(policies.length).toBe(buckets.length);
      for (const [, p] of policies) {
        const st = asArray<any>(p.Properties!.PolicyDocument.Statement);
        expect(st).toEqual(expect.arrayContaining([expect.objectContaining({
          Effect: 'Deny', Condition: { Bool: { 'aws:SecureTransport': 'false' } } })]));
        for (const x of st.filter((y) => y.Effect === 'Allow')) {
          expect(x.Principal).toEqual({ Service: 'cloudfront.amazonaws.com' }); // OAC only, never '*'
        }
      }
    });

    test('DynamoDB encrypted at rest; secrets in Secrets Manager; SNS topics TLS-only', () => {
      for (const [id, t] of ofType('AWS::DynamoDB::Table')) {
        expect({ id, sse: t.Properties!.SSESpecification?.SSEEnabled }).toEqual({ id, sse: true });
      }
      for (const [, topic] of ofType('AWS::SNS::Topic')) expect(topic).toBeDefined();
      for (const [, tp] of ofType('AWS::SNS::TopicPolicy')) {
        expect(JSON.stringify(tp.Properties)).toContain('"aws:SecureTransport":"false"');
      }
      expect(ofType('AWS::SNS::Topic').length).toBe(ofType('AWS::SNS::TopicPolicy').length);
    });

    // ------------------------------------------------------------------ 31.5 TLS for client traffic
    test('client traffic is HTTPS-only with TLS 1.2+ wherever the template controls it', () => {
      for (const [, d] of ofType('AWS::CloudFront::Distribution')) {
        const cfg = d.Properties!.DistributionConfig;
        for (const b of [cfg.DefaultCacheBehavior, ...asArray<any>(cfg.CacheBehaviors)]) {
          expect(['redirect-to-https', 'https-only']).toContain(b.ViewerProtocolPolicy);
        }
        for (const o of asArray<any>(cfg.Origins)) {
          if (o.CustomOriginConfig) expect(o.CustomOriginConfig.OriginProtocolPolicy).toBe('https-only');
        }
        if (ctx['safepath:certificateArn']) {
          expect(cfg.ViewerCertificate.MinimumProtocolVersion).toBe('TLSv1.2_2021');
          expect(cfg.ViewerCertificate.SslSupportMethod).toBe('sni-only');
        }
      }
      for (const [, dn] of ofType('AWS::ApiGatewayV2::DomainName')) {
        for (const c of asArray<any>(dn.Properties!.DomainNameConfigurations)) expect(c.SecurityPolicy).toBe('TLS_1_2');
      }
    });

    if (ctx.enableCloudFront === 'true' && !ctx['safepath:certificateArn']) {
      test('default *.cloudfront.net certificate: synth warns that TLS 1.2 minimum needs a custom certificate', () => {
        Annotations.fromStack(s.web).hasWarning('*', Match.stringLikeRegexp('TLS'));
      });
    }

    // ------------------------------------------------------------------ 31.1 per-function IAM
    test('each Lambda has its own role, each policy attaches to exactly one role, no wildcards', () => {
      const fns = ofType('AWS::Lambda::Function');
      const roleOf = (f: Res) => f.Properties!.Role['Fn::GetAtt'][0] as string;
      const roles = fns.map(([, f]) => roleOf(f));
      expect(new Set(roles).size).toBe(fns.length);
      for (const [id, p] of ofType('AWS::IAM::Policy')) {
        expect({ id, roles: asArray(p.Properties!.Roles).length }).toEqual({ id, roles: 1 });
        expect(p.Properties!.Users).toBeUndefined();
        expect(p.Properties!.Groups).toBeUndefined();
        for (const st of asArray<any>(p.Properties!.PolicyDocument.Statement)) {
          for (const a of asArray<string>(st.Action)) expect({ id, a }).not.toEqual({ id, a: expect.stringMatching(/^\*$|:\*$/) });
          for (const r of asArray<unknown>(st.Resource)) expect({ id, r }).not.toEqual({ id, r: '*' });
          expect(st.NotAction).toBeUndefined();
          expect(st.NotResource).toBeUndefined();
        }
      }
      for (const [id, r] of ofType('AWS::IAM::Role')) {
        const managed = JSON.stringify(r.Properties!.ManagedPolicyArns ?? []);
        expect({ id, admin: /AdministratorAccess|PowerUserAccess|FullAccess/.test(managed) }).toEqual({ id, admin: false });
      }
    });

    test('function grants are scoped to what each function uses', () => {
      const apiTpl = s.templates.get(s.api)!.toJSON().Resources as Record<string, Res>;
      const policyFor = (fnLogicalId: string): string => {
        const role = apiTpl[fnLogicalId].Properties!.Role['Fn::GetAtt'][0];
        const docs = Object.values(apiTpl).filter((r) => r.Type === 'AWS::IAM::Policy'
          && asArray<any>(r.Properties!.Roles).some((x) => x.Ref === role));
        return JSON.stringify(docs.map((d) => d.Properties!.PolicyDocument));
      };
      const fnId = (fn: { node: { defaultChild?: unknown } }) => s.api.resolve((fn.node.defaultChild as CfnResource).logicalId);
      // Table logical ids are Table<name without underscores><hash>; references appear in imported ARNs.
      const tablesIn = (json: string) => new Set([...json.matchAll(/Table([a-z]+)[0-9A-F]{8}/g)].map((m) => m[1]));
      const norm = (names: readonly string[]) => new Set(names.map((n) => n.replace(/_/g, '')));

      const apiPol = policyFor(fnId(s.api.apiFunction));
      expect(tablesIn(apiPol)).toEqual(norm(TABLE_SPECS.map((t) => t.name)));
      expect(apiPol).toContain('graphs/*');
      expect(apiPol).toContain('photos/*');
      expect(apiPol).toContain('secretsmanager:GetSecretValue');
      expect(apiPol).not.toMatch(/s3:DeleteObject|s3:PutBucket|s3:PutObjectAcl/);

      const sweeperPol = policyFor(fnId(s.api.sweeperFunction));
      expect(tablesIn(sweeperPol)).toEqual(norm(SWEEPER_TABLES));
      expect(sweeperPol).not.toMatch(/"s3:|secretsmanager:|bedrock:|cognito/);

      if (s.api.websocketFunction) {
        const wsPol = policyFor(fnId(s.api.websocketFunction));
        expect(tablesIn(wsPol)).toEqual(norm(WEBSOCKET_TABLES));
        expect(wsPol).not.toMatch(/"s3:|secretsmanager:|bedrock:|execute-api:/);
      }
      if (ctx.enableBedrock === 'true') {
        expect(apiPol).toContain('bedrock:InvokeModel');
        expect(apiPol).toMatch(/foundation-model\/[\w.:-]+/); // one model, not foundation-model/*
        expect(apiPol).not.toContain('foundation-model/*');
      } else {
        expect(apiPol).not.toContain('bedrock:');
      }
    });
  });
}
