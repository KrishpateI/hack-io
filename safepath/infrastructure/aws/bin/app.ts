#!/usr/bin/env node
import { App } from 'aws-cdk-lib';
import { resolveConfig } from '../lib/config';
import { ApiStack } from '../lib/api-stack';
import { DataStack } from '../lib/data-stack';
import { BudgetStack, budgetEnabled, budgetSkipReason } from '../lib/budget-stack';
import { ObservabilityStack } from '../lib/observability-stack';
import { applyProjectTags } from '../lib/tagging';
import { WebStack } from '../lib/web-stack';

const app = new App();
const config = resolveConfig(app.node);

// Environment comes from explicit context / CDK_DEFAULT_* only; no lookups, no hard-coded account.
const env = config.region ? { region: config.region, account: config.account } : undefined;

const data = new DataStack(app, `${config.prefix}-data`, { env, config });
const api = new ApiStack(app, `${config.prefix}-api`, { env, config, data });

// Optional stacks are independent; skip any with -c safepath:skip=web,observability,budget
const skip = new Set(String(app.node.tryGetContext('safepath:skip') ?? '').split(',').map((s) => s.trim()).filter(Boolean));
if (!skip.has('web')) new WebStack(app, `${config.prefix}-web`, { env, config, api });
if (!skip.has('observability')) new ObservabilityStack(app, `${config.prefix}-observability`, { env, config, api, data });
if (!skip.has('budget')) {
  if (budgetEnabled(config)) new BudgetStack(app, `${config.prefix}-budget`, { env, config });
  else console.warn(`[safepath] budget stack skipped: ${budgetSkipReason(config)}`);
}


applyProjectTags(app, config.environment);
