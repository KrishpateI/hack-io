# AWS deployment

## Sandbox discovery

Generated: 2026-10-24T18:44:25+00:00 (read-only probes; nothing was created, modified or deleted).

- Status: `unavailable`
- Region: `not set`
- Deployable (gate): `false`
- Reason: boto3 not installed, no credentials configured, or no region set

### Steps to enable discovery

1. Install the AWS CLI v2 (https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html).
2. Install boto3: backend\.venv\Scripts\python -m pip install -e "backend[aws]" (from safepath/).
3. Configure sandbox credentials: `aws configure` or set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (and AWS_SESSION_TOKEN) plus AWS_DEFAULT_REGION.
4. Re-run: backend\.venv\Scripts\python scripts\aws_discover.py

### Substitution matrix

| Feature | State | Labeled substitute | CDK flag |
|---|---|---|---|
| Real-time delivery: AppSync Events | unknown | API Gateway WebSocket | `-c realtime=apigw-ws` |
| Authentication: Amazon Cognito | unknown | Backend-signed demo tokens (DemoTokenAuthProvider) | `-c useCognito=false` |
| Frontend CDN: CloudFront | unknown | S3 static website hosting (documented alternative) | `-c enableCloudFront=false` |
| AI summaries: Amazon Bedrock | unknown | AI feature disabled (UI shows 'AI summaries unavailable') | `-c enableBedrock=false` |
| Cost guard: AWS Budgets | unknown | No budget stack; monitor cost manually | `-c enableBudget=false` |

### CDK context flags

`-c useCognito=false -c realtime=apigw-ws -c enableBudget=false -c enableBedrock=false -c enableCloudFront=false`

Gate file: `infrastructure/aws/discovery.json` (the deploy script requires `deployable: true`).
