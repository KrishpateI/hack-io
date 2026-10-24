import { CfnOutput, Duration, Stack, StackProps } from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cwActions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as subs from 'aws-cdk-lib/aws-sns-subscriptions';
import { Construct } from 'constructs';
import { SafePathConfig } from './config';
import { ApiStack } from './api-stack';
import { DataStack } from './data-stack';

export interface ObservabilityStackProps extends StackProps {
  readonly config: SafePathConfig;
  readonly api: ApiStack;
  readonly data: DataStack;
}

/**
 * CloudWatch dashboard plus alarms for the API, Lambda, DynamoDB and the sweeper.
 * Log groups (7-day retention) and the basic Lambda error/throttle alarms already live in ApiStack;
 * this stack adds the dashboard, 5xx/latency/sweeper-liveness/DynamoDB alarms and optional email delivery.
 * With `-c safepath:alertEmail=<addr>` an SNS topic and an email subscription are created (the address must
 * confirm the subscription); without it the alarms exist but have no action and no topic is created.
 */
export class ObservabilityStack extends Stack {
  public readonly dashboard: cloudwatch.Dashboard;
  public readonly alertTopic?: sns.Topic;

  constructor(scope: Construct, id: string, props: ObservabilityStackProps) {
    super(scope, id, props);
    const { config, api, data } = props;
    const five = Duration.minutes(5);

    if (config.alertEmail) {
      this.alertTopic = new sns.Topic(this, 'AlertTopic', {
        topicName: `${config.prefix}-alerts`,
        enforceSSL: true,
      });
      this.alertTopic.addSubscription(new subs.EmailSubscription(config.alertEmail));
      new CfnOutput(this, 'AlertTopicArn', { value: this.alertTopic.topicArn });
    }
    const notify = (alarm: cloudwatch.Alarm) => {
      if (this.alertTopic) alarm.addAlarmAction(new cwActions.SnsAction(this.alertTopic));
      return alarm;
    };

    // ---------------------------------------------------------------- metrics
    const apiCount = api.httpApi.metricCount({ period: five, statistic: 'Sum' });
    const api4xx = api.httpApi.metricClientError({ period: five, statistic: 'Sum' });
    const api5xx = api.httpApi.metricServerError({ period: five, statistic: 'Sum' });
    const apiLatencyP95 = api.httpApi.metricLatency({ period: five, statistic: 'p95' });
    const apiLatencyP99 = api.httpApi.metricLatency({ period: five, statistic: 'p99' });
    // HTTP APIs publish no throttle metric; throttled calls surface as 429s in 4xx and as Lambda throttles.
    const lambdaThrottles = api.apiFunction.metricThrottles({ period: five, statistic: 'Sum' });

    const fnMetrics = (fn: ApiStack['apiFunction']) => ({
      duration: fn.metricDuration({ period: five, statistic: 'p95' }),
      durationMax: fn.metricDuration({ period: five, statistic: 'Maximum' }),
      errors: fn.metricErrors({ period: five, statistic: 'Sum' }),
      invocations: fn.metricInvocations({ period: five, statistic: 'Sum' }),
    });
    const apiFn = fnMetrics(api.apiFunction);
    const sweeper = fnMetrics(api.sweeperFunction);

    const tables = Object.values(data.tables);
    // One expression may only use unique metric ids, so ids are <prefix><index> across all metric names.
    const sumAcrossTables = (metricNames: string[], prefix: string, label: string, subset: typeof tables = tables) => {
      const using: Record<string, cloudwatch.IMetric> = {};
      const ids: string[] = [];
      for (const [mi, name] of metricNames.entries()) {
        subset.forEach((t, i) => {
          const id = `${prefix}${mi}x${i}`;
          using[id] = t.metric(name, { period: five, statistic: 'Sum' });
          ids.push(id);
        });
      }
      return new cloudwatch.MathExpression({ expression: ids.join('+'), usingMetrics: using, period: five, label });
    };    const ddbReadThrottles = sumAcrossTables(['ReadThrottleEvents'], 'r', 'Read throttle events');
    const ddbWriteThrottles = sumAcrossTables(['WriteThrottleEvents'], 'w', 'Write throttle events');
    const ddbReadUnits = sumAcrossTables(['ConsumedReadCapacityUnits'], 'c', 'Consumed read units');
    const ddbWriteUnits = sumAcrossTables(['ConsumedWriteCapacityUnits'], 'u', 'Consumed write units');

    // CloudWatch alarms allow at most 10 metrics per expression: alarm on the five busiest tables (dashboard shows all).
    const hotTables = ['incidents', 'reports', 'events', 'ratelimits', 'connections'].map((n) => data.tables[n]);

    // ---------------------------------------------------------------- dashboard
    this.dashboard = new cloudwatch.Dashboard(this, 'Dashboard', {
      dashboardName: `${config.prefix}-overview`,
      defaultInterval: Duration.hours(3),
    });
    this.dashboard.addWidgets(
      new cloudwatch.TextWidget({
        width: 24, height: 2,
        markdown: '# SafePath (SIMULATED sandbox)\nAPI, Lambda, DynamoDB and sweeper health. '
          + 'Memory: API function is configured for 3008 MB; enable Lambda Insights for used-memory graphs.',
      }),
      new cloudwatch.GraphWidget({ title: 'API requests', left: [apiCount], width: 8 }),
      new cloudwatch.GraphWidget({ title: 'API errors (4xx / 5xx)', left: [api4xx, api5xx], width: 8 }),
      new cloudwatch.GraphWidget({ title: 'API latency (p95 / p99, ms)', left: [apiLatencyP95, apiLatencyP99], width: 8 }),
      new cloudwatch.GraphWidget({ title: 'Throttles (API Lambda)', left: [lambdaThrottles], width: 8 }),
      new cloudwatch.GraphWidget({
        title: 'Lambda duration (p95 / max, ms)', left: [apiFn.duration, apiFn.durationMax, sweeper.duration], width: 8,
      }),
      new cloudwatch.GraphWidget({ title: 'Lambda errors', left: [apiFn.errors, sweeper.errors], width: 8 }),
      new cloudwatch.GraphWidget({ title: 'DynamoDB throttle events', left: [ddbReadThrottles, ddbWriteThrottles], width: 12 }),
      new cloudwatch.GraphWidget({ title: 'DynamoDB consumed capacity', left: [ddbReadUnits, ddbWriteUnits], width: 12 }),
      new cloudwatch.GraphWidget({ title: 'Sweeper invocations (expected: 5 per 5 min)', left: [sweeper.invocations], width: 12 }),
      new cloudwatch.GraphWidget({ title: 'Sweeper errors', left: [sweeper.errors], width: 12 }),
    );

    // ---------------------------------------------------------------- alarms
    notify(new cloudwatch.Alarm(this, 'Api5xxAlarm', {
      alarmName: `${config.prefix}-api-5xx`,
      metric: api5xx, threshold: 5, evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }));
    notify(new cloudwatch.Alarm(this, 'ApiLatencyAlarm', {
      alarmName: `${config.prefix}-api-latency-p99`,
      metric: apiLatencyP99, threshold: 10_000, evaluationPeriods: 2, // ms; API Gateway cuts off at ~30 s
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }));
    notify(new cloudwatch.Alarm(this, 'ApiLambdaErrorsAlarm', {
      alarmName: `${config.prefix}-api-lambda-errors`,
      metric: apiFn.errors, threshold: 5, evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }));
    notify(new cloudwatch.Alarm(this, 'SweeperStalledAlarm', {
      alarmName: `${config.prefix}-sweeper-stalled`,
      metric: sweeper.invocations, threshold: 1, evaluationPeriods: 2,
      comparisonOperator: cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.BREACHING, // no datapoints = the 1-minute rule is not firing
    }));
    notify(new cloudwatch.Alarm(this, 'DynamoThrottleAlarm', {
      alarmName: `${config.prefix}-dynamodb-throttles`,
      metric: sumAcrossTables(['ReadThrottleEvents', 'WriteThrottleEvents'], 'a', 'DynamoDB throttle events (hot tables)', hotTables),
      threshold: 1, evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    }));

    new CfnOutput(this, 'DashboardName', { value: `${config.prefix}-overview` });
  }
}
