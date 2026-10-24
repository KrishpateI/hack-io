import * as fs from 'fs';
import * as path from 'path';
import { Node } from 'constructs';

/** Feature flags. Conservative defaults: every optional service is OFF unless explicitly true. */
export interface SafePathConfig {
  readonly prefix: string;
  readonly environment: string;
  readonly useCognito: boolean;
  readonly realtime: 'appsync' | 'apigw-ws';
  readonly enableBudget: boolean;
  readonly enableBedrock: boolean;
  readonly enableCloudFront: boolean;
  readonly frontendOrigins: string[];
  readonly photoRetentionDays: number;
  /** safepath:alertEmail: alarm + budget notifications. Unset = no subscription / no budget. */
  readonly alertEmail?: string;
  /** safepath:budgetUsd: monthly cost budget (default 25). */
  readonly budgetUsd: number;
  readonly region?: string;
  readonly account?: string;
}

const DEFAULT_ORIGINS = ['http://localhost:5173', 'http://127.0.0.1:5173'];

const truthy = (v: unknown): boolean => String(v ?? '').trim().toLowerCase() === 'true';

function readDiscoveryFlags(file: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(fs.readFileSync(file, 'utf-8'));
    const flags = parsed?.context_flags;
    return flags && typeof flags === 'object' ? flags : {};
  } catch {
    return {};
  }
}

/**
 * Resolve configuration. Precedence: `-c key=value` CDK context > discovery.json context_flags > default.
 * discovery.json is optional. Nothing here is a secret.
 */
export function resolveConfig(node: Node, discoveryFile = path.join(__dirname, '..', 'discovery.json')): SafePathConfig {
  const discovered = readDiscoveryFlags(discoveryFile);
  const get = (key: string): unknown => {
    const ctx = node.tryGetContext(key);
    return ctx !== undefined ? ctx : discovered[key];
  };
  const realtimeRaw = String(get('realtime') ?? 'apigw-ws');
  const origins = String(node.tryGetContext('safepath:frontendOrigins') ?? '')
    .split(',').map((s) => s.trim()).filter(Boolean);
  const days = Number(node.tryGetContext('safepath:photoRetentionDays') ?? 30);
  const email = String(node.tryGetContext('safepath:alertEmail') ?? '').trim();
  if (email && !/^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$/.test(email)) {
    throw new Error('safepath:alertEmail must be a single valid email address');
  }
  const usd = Number(node.tryGetContext('safepath:budgetUsd') ?? 25);
  return {
    prefix: String(node.tryGetContext('safepath:prefix') ?? 'safepath'),
    environment: String(node.tryGetContext('safepath:environment') ?? 'sandbox'),
    useCognito: truthy(get('useCognito')),
    realtime: realtimeRaw === 'appsync' ? 'appsync' : 'apigw-ws',
    enableBudget: truthy(get('enableBudget')),
    enableBedrock: truthy(get('enableBedrock')),
    enableCloudFront: truthy(get('enableCloudFront')),
    frontendOrigins: origins.length ? origins : DEFAULT_ORIGINS,
    photoRetentionDays: Number.isFinite(days) && days > 0 ? Math.floor(days) : 30,
    alertEmail: email || undefined,
    budgetUsd: Number.isFinite(usd) && usd > 0 ? usd : 25,
    region: node.tryGetContext('safepath:region') ?? process.env.CDK_DEFAULT_REGION,
    account: node.tryGetContext('safepath:account') ?? process.env.CDK_DEFAULT_ACCOUNT,
  };
}
