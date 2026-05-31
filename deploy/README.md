# Deploying Attest to AWS App Runner

Attest is a single ASGI service. For this v1 the fact store and the hash-chained
audit log live **in process memory**, so run **exactly one instance** until a
persistent store lands — every replica would otherwise hold a different fact
store and a divergent audit chain.

App Runner needs **two IAM roles**:

| Role | Trusts | Purpose |
| --- | --- | --- |
| **access role** | `build.apprunner.amazonaws.com` | lets App Runner *pull the image* from a private ECR repo |
| **instance role** | `tasks.apprunner.amazonaws.com` | lets the *running container* read its secrets |

Set these once, then redeploys are just `docker push` (auto-deploy is on).

```bash
export AWS_REGION=us-east-1
export ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
export REPO=$ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com/attest
```

## 1. Build and push the image to ECR

```bash
aws ecr create-repository --repository-name attest --region $AWS_REGION
aws ecr get-login-password --region $AWS_REGION \
  | docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$AWS_REGION.amazonaws.com

docker build -t attest .
docker tag attest:latest $REPO:latest
docker push $REPO:latest
```

## 2. Store secrets (you have none until the Postgres swap / LLM edge — set them then)

```bash
# DB connection string -> Secrets Manager
aws secretsmanager create-secret --name attest/database-url \
  --secret-string 'postgresql://USER:PASS@HOST:5432/attest' --region $AWS_REGION

# LLM key -> SSM Parameter Store (SecureString; free tier)
aws ssm put-parameter --name /attest/anthropic-api-key --type SecureString \
  --value 'sk-ant-...' --region $AWS_REGION
```

Secrets are resolved **at container start**, not live — rotating a value
requires a redeploy (or it's picked up on the next instance start).

## 3. Create the access role (ECR pull)

```bash
aws iam create-role --role-name attest-apprunner-access-role \
  --assume-role-policy-document file://deploy/iam-access-role-trust.json
aws iam attach-role-policy --role-name attest-apprunner-access-role \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess
```

## 4. Create the instance role (read secrets)

Edit `deploy/iam-instance-role-policy.json` to replace `<REGION>` / `<ACCOUNT_ID>`
(and drop the `kms:Decrypt` statement unless you used a customer-managed key).

```bash
aws iam create-role --role-name attest-apprunner-instance-role \
  --assume-role-policy-document file://deploy/iam-instance-role-trust.json
aws iam put-role-policy --role-name attest-apprunner-instance-role \
  --policy-name attest-secrets-read \
  --policy-document file://deploy/iam-instance-role-policy.json
```

## 5. Pin to a single instance

In-memory state means max size **1**.

```bash
aws apprunner create-auto-scaling-configuration \
  --auto-scaling-configuration-name attest-single \
  --max-size 1 --min-size 1 --region $AWS_REGION
# note the returned AutoScalingConfigurationArn for step 6
```

## 6. Create the service

Edit `deploy/apprunner-service.json` to replace `<ACCOUNT_ID>` / `<REGION>`
(remove the `RuntimeEnvironmentSecrets` entries you haven't created yet, or the
service will fail to start trying to resolve them). Then:

```bash
aws apprunner create-service \
  --cli-input-json file://deploy/apprunner-service.json \
  --auto-scaling-configuration-arn <ARN_FROM_STEP_5> \
  --region $AWS_REGION
```

App Runner returns a `*.awsapprunner.com` URL with TLS. It health-checks
`GET /health` (cheap liveness). `GET /ready` additionally re-derives the audit
hash-chain and returns 503 if it's ever broken — wire that into an external
uptime/integrity monitor. `GET /audit/verify` is the same check as a JSON API.

## Notes / what's deliberately not here

- **The image carries no secrets.** Only ARNs are referenced; values are injected
  at runtime. Plaintext `RuntimeEnvironmentVariables` (e.g. `LOG_LEVEL`) are *not*
  secret and are visible in the service config — keep credentials out of them.
- **Scaling past one instance** requires the Postgres-backed `FactStore` /
  `AuditLog` first. After that, raise the auto-scaling max and you're stateless.
- **Fargate** uses the same secrets model via the task definition's `secrets`
  block and the task *execution* role — see the main README for when to graduate.
