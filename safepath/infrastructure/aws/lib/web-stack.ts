import { Annotations, Aws, CfnOutput, Duration, Fn, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as acm from 'aws-cdk-lib/aws-certificatemanager';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3 from 'aws-cdk-lib/aws-s3';
import { Construct } from 'constructs';
import { SafePathConfig } from './config';

/** Minimal view of ApiStack that WebStack needs (kept structural so WebStack is testable alone). */
export interface WebApiRefs {
  readonly httpApi: { readonly apiEndpoint: string };
  readonly webSocketApi?: { readonly apiEndpoint: string };
  readonly eventApi?: { readonly httpDns: string; readonly realtimeDns: string };
}

export interface WebStackProps extends StackProps {
  readonly config: SafePathConfig;
  /** When given, CloudFront proxies /api/* to the HTTP API and the CSP allows its endpoints. */
  readonly api?: WebApiRefs;
}

/** Context values `safepath:webHosting`. */
export type WebHosting = 'none' | 's3-website';

/** Paths that must never be served stale: the SPA shell and the service worker. */
export const NO_CACHE_PATHS = ['index.html', 'sw.js', 'service-worker.js', 'registerSW.js', 'manifest.webmanifest'] as const;

/** CloudFront Function body: extensionless, non-/api paths become /index.html. */
export const SPA_FALLBACK_JS = [
  'function handler(event) {',
  '  var request = event.request;',
  '  var uri = request.uri;',
  "  if (uri.indexOf('/api/') !== 0 && uri.lastIndexOf('.') <= uri.lastIndexOf('/')) request.uri = '/index.html';",
  '  return request;',
  '}',
].join('\n');

/** Builds the Content-Security-Policy. connect-src is limited to self, the SafePath API/WebSocket/AppSync endpoints and tile sources. */
export function buildCsp(connectSources: string[], extraImgSources: string[] = []): string {
  const connect = ["'self'", 'blob:', 'data:', ...connectSources].join(' ');
  const img = ["'self'", 'data:', 'blob:', ...extraImgSources].join(' ');
  return [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'", // MapLibre injects inline styles
    `img-src ${img}`,
    "font-src 'self' data:",
    `connect-src ${connect}`,
    "worker-src 'self' blob:", // service worker + MapLibre web workers
    "child-src 'self' blob:",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join('; ');
}

/**
 * Static hosting for the built frontend (frontend/dist), PMTiles and static GeoJSON layers.
 *
 * - The site bucket is ALWAYS private (block all public access, S3-managed SSE, enforceSSL).
 * - `enableCloudFront=true`: CloudFront + Origin Access Control in front of it (HTTPS only, TLS >= 1.2
 *   when a custom certificate is supplied; see README for the default-certificate caveat).
 * - Otherwise no public hosting resources are created (serve locally with `npm run dev` / `npm run preview`).
 *   `-c safepath:webHosting=s3-website` explicitly opts in to a SEPARATE public-read HTTP-only bucket.
 * - No BucketDeployment (it adds a custom-resource Lambda); upload with `aws s3 sync` (task 12.6).
 */
export class WebStack extends Stack {
  public readonly siteBucket: s3.Bucket;
  public readonly publicWebsiteBucket?: s3.Bucket;
  public readonly distribution?: cloudfront.Distribution;

  constructor(scope: Construct, id: string, props: WebStackProps) {
    super(scope, id, props);
    const { config, api } = props;
    const ctx = (k: string) => this.node.tryGetContext(k);
    const suffix = `${Aws.ACCOUNT_ID}-${Aws.REGION}`;

    this.siteBucket = new s3.Bucket(this, 'SiteBucket', {
      bucketName: `${config.prefix}-site-${suffix}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: false, // would add a custom-resource Lambda; empty the bucket before destroy
      lifecycleRules: [{ id: 'abort-multipart', abortIncompleteMultipartUploadAfter: Duration.days(1) }],
    });
    new CfnOutput(this, 'SiteBucketName', { value: this.siteBucket.bucketName });
    new CfnOutput(this, 'SiteSyncCommand', {
      value: `aws s3 sync frontend/dist s3://${this.siteBucket.bucketName} --delete`,
      description: 'Upload the built frontend (task 12.6 script wraps this). PMTiles go under tiles/, GeoJSON under layers/.',
    });

    const hosting: WebHosting = String(ctx('safepath:webHosting') ?? 'none') === 's3-website' ? 's3-website' : 'none';

    if (config.enableCloudFront) {
      if (hosting === 's3-website') {
        Annotations.of(this).addWarningV2('safepath:web-hosting-ignored',
          'safepath:webHosting=s3-website ignored because enableCloudFront=true; the bucket stays private.');
      }
      this.distribution = this.buildDistribution(config, api, ctx);
      new CfnOutput(this, 'DistributionDomainName', { value: this.distribution.distributionDomainName });
      new CfnOutput(this, 'SiteUrl', { value: `https://${this.distribution.distributionDomainName}` });
      new CfnOutput(this, 'ApiViaCloudFront', {
        value: `https://${this.distribution.distributionDomainName}/api`,
        description: 'Same-origin API base (no CORS). WebSockets connect directly to WebSocketUrl of the api stack.',
      });
    } else if (hosting === 's3-website') {
      Annotations.of(this).addWarningV2('safepath:public-website-bucket',
        'PUBLIC website bucket requested (safepath:webHosting=s3-website): HTTP only, world-readable. Sandbox demo use only.');
      this.publicWebsiteBucket = new s3.Bucket(this, 'PublicWebsiteBucket', {
        bucketName: `${config.prefix}-public-website-${suffix}`,
        websiteIndexDocument: 'index.html',
        websiteErrorDocument: 'index.html', // SPA fallback
        publicReadAccess: true,
        blockPublicAccess: new s3.BlockPublicAccess({
          blockPublicAcls: true, ignorePublicAcls: true, blockPublicPolicy: false, restrictPublicBuckets: false,
        }),
        encryption: s3.BucketEncryption.S3_MANAGED,
        removalPolicy: RemovalPolicy.DESTROY,
        autoDeleteObjects: false,
      });
      new CfnOutput(this, 'PublicWebsiteUrl', {
        value: this.publicWebsiteBucket.bucketWebsiteUrl,
        description: 'WARNING: public, HTTP only (no TLS). Upload with: aws s3 sync frontend/dist s3://<PublicWebsiteBucketName>',
      });
      new CfnOutput(this, 'PublicWebsiteBucketName', { value: this.publicWebsiteBucket.bucketName });
    } else {
      new CfnOutput(this, 'HostingInstructions', {
        value: 'No public hosting (default). Run the frontend locally: cd frontend && npm run dev (or npm run build && npm run preview). '
          + 'Enable CloudFront with -c enableCloudFront=true, or opt in to -c safepath:webHosting=s3-website (public, HTTP only).',
      });
    }
  }

  private buildDistribution(
    config: SafePathConfig, api: WebApiRefs | undefined, ctx: (k: string) => unknown,
  ): cloudfront.Distribution {
    // ---- CSP: connect-src to the API / WebSocket / AppSync endpoints and tile sources
    const connect: string[] = [];
    if (api) {
      connect.push(api.httpApi.apiEndpoint);
      if (api.webSocketApi) connect.push(api.webSocketApi.apiEndpoint);
      if (api.eventApi) connect.push(`https://${api.eventApi.httpDns}`, `wss://${api.eventApi.realtimeDns}`);
    }
    const tileSources = String(ctx('safepath:tileSources') ?? '').split(',').map((s) => s.trim()).filter(Boolean);
    connect.push(...tileSources);
    const csp = buildCsp(connect, tileSources);

    const headers = new cloudfront.ResponseHeadersPolicy(this, 'SecurityHeaders', {
      comment: 'SafePath security headers',
      securityHeadersBehavior: {
        contentSecurityPolicy: { contentSecurityPolicy: csp, override: true },
        strictTransportSecurity: { accessControlMaxAge: Duration.days(365), includeSubdomains: true, override: true },
        contentTypeOptions: { override: true },
        frameOptions: { frameOption: cloudfront.HeadersFrameOption.DENY, override: true },
        referrerPolicy: { referrerPolicy: cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN, override: true },
      },
      customHeadersBehavior: {
        customHeaders: [{ header: 'Permissions-Policy', value: 'geolocation=(self), camera=(), microphone=()', override: true }],
      },
    });

    // ---- caching
    const noCache = cloudfront.CachePolicy.CACHING_DISABLED; // index.html + service worker: always revalidate at the edge
    const immutable = new cloudfront.CachePolicy(this, 'ImmutableAssets', {
      comment: 'Hashed assets (assets/*): long-lived',
      defaultTtl: Duration.days(365), minTtl: Duration.days(1), maxTtl: Duration.days(365),
      enableAcceptEncodingGzip: true, enableAcceptEncodingBrotli: true,
    });
    // PMTiles: CloudFront handles HTTP Range natively (it forwards Range to S3 and serves 206 from cache);
    // Range is not part of the cache key. Disable compression so byte offsets stay valid.
    const tiles = cloudfront.CachePolicy.CACHING_OPTIMIZED;

    const s3Origin = origins.S3BucketOrigin.withOriginAccessControl(this.siteBucket);
    const baseBehavior: cloudfront.BehaviorOptions = {
      origin: s3Origin,
      viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
      allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
      responseHeadersPolicy: headers,
      compress: true,
    };

    const additionalBehaviors: Record<string, cloudfront.BehaviorOptions> = {
      'assets/*': { ...baseBehavior, cachePolicy: immutable },
      'tiles/*': { ...baseBehavior, cachePolicy: tiles, compress: false },
      'layers/*': { ...baseBehavior, cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED },
    };
    for (const p of NO_CACHE_PATHS) additionalBehaviors[p] = { ...baseBehavior, cachePolicy: noCache };

    if (api) {
      // https://<id>.execute-api.<region>.amazonaws.com -> host only
      const apiHost = Fn.select(2, Fn.split('/', api.httpApi.apiEndpoint));
      additionalBehaviors['api/*'] = {
        origin: new origins.HttpOrigin(apiHost, { protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY }),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
        // Forwards Authorization, Idempotency-Key, query strings; Host is the API's own.
        originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
        responseHeadersPolicy: headers,
      };
    }

    // SPA fallback. Distribution-wide errorResponses (403/404 -> /index.html) would also rewrite the API's own
    // 403/404 JSON errors (role checks, missing resources), so the fallback is a viewer-request function on the
    // default (SPA) behavior only: extensionless paths are served as /index.html, missing files keep their 404.
    const spaFallback = new cloudfront.Function(this, 'SpaFallback', {
      comment: 'Rewrite extensionless SPA routes to /index.html',
      runtime: cloudfront.FunctionRuntime.JS_2_0,
      code: cloudfront.FunctionCode.fromInline(SPA_FALLBACK_JS),
    });

    const domainName = ctx('safepath:domainName') as string | undefined;
    const certArn = ctx('safepath:certificateArn') as string | undefined;
    const customDomain = domainName && certArn;
    if (!customDomain) {
      // Req 31.5: CloudFront ignores minimumProtocolVersion on the default certificate (TLSv1 policy).
      Annotations.of(this).addWarningV2('safepath:cloudfront-default-cert-tls',
        'CloudFront uses the default *.cloudfront.net certificate, which accepts TLS 1.0+; the TLS 1.2 minimum is only '
        + 'enforced with -c safepath:domainName=<host> -c safepath:certificateArn=<us-east-1 ACM ARN>.');
    }

    return new cloudfront.Distribution(this, 'Distribution', {
      comment: `${config.prefix} SPA, PMTiles and GeoJSON`,
      defaultRootObject: 'index.html',
      defaultBehavior: {
        ...baseBehavior,
        cachePolicy: noCache,
        functionAssociations: [{ function: spaFallback, eventType: cloudfront.FunctionEventType.VIEWER_REQUEST }],
      },
      additionalBehaviors,
      minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021,
      httpVersion: cloudfront.HttpVersion.HTTP2_AND_3,
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      ...(customDomain ? {
        domainNames: [domainName],
        certificate: acm.Certificate.fromCertificateArn(this, 'Cert', certArn), // must be in us-east-1
      } : {}),
    });
  }
}
