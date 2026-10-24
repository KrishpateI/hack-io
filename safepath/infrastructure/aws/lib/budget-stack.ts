import { CfnOutput, Stack, StackProps } from 'aws-cdk-lib';
import * as budgets from 'aws-cdk-lib/aws-budgets';
import { Construct } from 'constructs';
import { SafePathConfig } from './config';

/** Why no budget would be created, or undefined when one will be. */
export function budgetSkipReason(config: SafePathConfig): string | undefined {
  if (!config.enableBudget) return 'enableBudget is not true';
  if (!config.alertEmail) return 'safepath:alertEmail is not set';
  return undefined;
}

export const budgetEnabled = (config: SafePathConfig): boolean => budgetSkipReason(config) === undefined;

export interface BudgetStackProps extends StackProps {
  readonly config: SafePathConfig;
}

/**
 * Monthly cost budget (default 25 USD, `-c safepath:budgetUsd=`). Instantiate only when budgetEnabled(config): `enableBudget=true`
 * AND `safepath:alertEmail` is set; otherwise app.ts skips the stack (an empty stack is invalid CloudFormation). Notifications: ACTUAL spend
 * at 80% and FORECASTED spend at 100%, both emailed to the alert address.
 *
 * Budgets are account-scoped (not per stack) and need billing permissions (budgets:*); the discovery script
 * reports availability and a sandbox may not allow it.
 */
export class BudgetStack extends Stack {
  public readonly budget?: budgets.CfnBudget;

  constructor(scope: Construct, id: string, props: BudgetStackProps) {
    super(scope, id, props);
    const { config } = props;

    if (!config.alertEmail || !budgetEnabled(config)) {
      // CloudFormation rejects templates without resources; app.ts only instantiates this stack when enabled.
      throw new Error(budgetSkipReason(config) ?? 'budget disabled');
    }
    const email = config.alertEmail;
    const notification = (type: 'ACTUAL' | 'FORECASTED', threshold: number): budgets.CfnBudget.NotificationWithSubscribersProperty => ({
      notification: {
        notificationType: type,
        comparisonOperator: 'GREATER_THAN',
        threshold,
        thresholdType: 'PERCENTAGE',
      },
      subscribers: [{ subscriptionType: 'EMAIL', address: email }],
    });

    this.budget = new budgets.CfnBudget(this, 'MonthlyBudget', {
      budget: {
        budgetName: `${config.prefix}-monthly`,
        budgetType: 'COST',
        timeUnit: 'MONTHLY',
        budgetLimit: { amount: config.budgetUsd, unit: 'USD' },
      },
      // CfnBudget is not an ITaggable, so the app-wide tag aspect skips it; tag explicitly.
      resourceTags: [
        { key: 'Project', value: 'SafePath' },
        { key: 'Environment', value: config.environment },
        { key: 'Simulated', value: 'true' },
      ],
      notificationsWithSubscribers: [notification('ACTUAL', 80), notification('FORECASTED', 100)],
    });
    new CfnOutput(this, 'BudgetStatus', { value: `created: ${config.budgetUsd} USD monthly, alerts to ${email}` });
  }
}
