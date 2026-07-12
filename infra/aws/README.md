# AWS Deployment (App Runner + Cognito)

This project can be deployed as two AWS App Runner services:

- `conversation-intel-backend` from `Dockerfile.backend`
- `conversation-intel-frontend` from `Dockerfile.frontend`

## Prerequisites

- AWS account with permissions for ECR, App Runner, IAM, Secrets Manager, DynamoDB
- AWS CLI configured (`aws configure`)
- Docker installed locally

## 1) Create ECR repositories

```bash
aws ecr create-repository --repository-name conversation-intel-backend
aws ecr create-repository --repository-name conversation-intel-frontend
```

## 2) Build and push images

Set these shell variables:

```bash
export AWS_REGION=us-east-1
export AWS_ACCOUNT_ID=<your_aws_account_id>
export BACKEND_REPO=conversation-intel-backend
export FRONTEND_REPO=conversation-intel-frontend
export IMAGE_TAG=v1
```

Login and push:

```bash
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

docker build -f Dockerfile.backend -t "$BACKEND_REPO:$IMAGE_TAG" .
docker tag "$BACKEND_REPO:$IMAGE_TAG" "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$BACKEND_REPO:$IMAGE_TAG"
docker push "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$BACKEND_REPO:$IMAGE_TAG"

docker build -f Dockerfile.frontend -t "$FRONTEND_REPO:$IMAGE_TAG" .
docker tag "$FRONTEND_REPO:$IMAGE_TAG" "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$FRONTEND_REPO:$IMAGE_TAG"
docker push "$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$FRONTEND_REPO:$IMAGE_TAG"
```

## 2.5) DynamoDB table (session persistence)

Create a table with partition key `pk` and sort key `sk` (both strings):

```bash
aws dynamodb create-table \
  --table-name conversation-intel-sessions \
  --attribute-definitions AttributeName=pk,AttributeType=S AttributeName=sk,AttributeType=S \
  --key-schema AttributeName=pk,KeyType=HASH AttributeName=sk,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --region "$AWS_REGION"
```

Grant the App Runner instance role `dynamodb:PutItem`, `dynamodb:GetItem`, `dynamodb:Query`, and `dynamodb:UpdateItem` on this table.

## 3) Deploy backend App Runner service

Use `infra/aws/backend.apprunner.yaml`:

```bash
aws apprunner create-service \
  --cli-input-yaml file://infra/aws/backend.apprunner.yaml
```

After service is created, set runtime environment variables in App Runner console:

- `ASSEMBLYAI_API_KEY` (Secret)
- `OPENAI_API_KEY` (Secret)
- `REQUIRE_API_AUTH=true`
- `AUTH_JWKS_URL=<cognito jwks url>`
- `AUTH_ISSUER=<cognito issuer>`
- `AUTH_AUDIENCE=<cognito app client id>`
- `BACKEND_CORS_ORIGINS=https://<frontend-domain>`
- `RATE_LIMIT_PER_MINUTE=30`
- `DAILY_REQUEST_QUOTA=2000`
- `STORAGE_BACKEND=dynamodb`
- `DYNAMODB_CONVERSATIONS_TABLE=conversation-intel-sessions`
- `AWS_REGION=<same as service region>`

## 4) Deploy frontend App Runner service

Use `infra/aws/frontend.apprunner.yaml`:

```bash
aws apprunner create-service \
  --cli-input-yaml file://infra/aws/frontend.apprunner.yaml
```

Set runtime environment variables:

- `NEXT_PUBLIC_BACKEND_URL=https://<backend-domain>`
- `NEXT_PUBLIC_COGNITO_DOMAIN=<your-cognito-domain>`
- `NEXT_PUBLIC_COGNITO_CLIENT_ID=<cognito app client id>`
- `NEXT_PUBLIC_COGNITO_REDIRECT_URI=https://<frontend-domain>`
- `NEXT_PUBLIC_COGNITO_LOGOUT_URI=https://<frontend-domain>`
- `NEXT_PUBLIC_COGNITO_RESPONSE_TYPE=token`
- `NEXT_PUBLIC_COGNITO_SCOPE=openid email profile`

## 5) Validate

- Open frontend URL
- Login via Cognito
- Start a session and verify transcript + suggestions
- Verify backend `/health` returns 200

## 6) Async inference worker (optional scale path)

For heavy OpenAI load, enable the job queue:

1. Create an SQS standard queue (or FIFO if you enforce ordering globally).
2. Grant the API instance role `sqs:SendMessage` on that queue URL.
3. Grant the worker instance role `sqs:ReceiveMessage`, `sqs:DeleteMessage`, and `sqs:GetQueueAttributes` on that queue URL.
4. Deploy a **second service** using `Dockerfile.worker` (ECS/Fargate is common; App Runner can run an extra service as well).
5. Configure backend and worker with matching env:
   - `ASYNC_JOBS_ENABLED=true`
   - `INFERENCE_QUEUE_MODE=sqs`
   - `AWS_SQS_INFERENCE_QUEUE_URL=https://sqs.<region>.amazonaws.com/<acct>/<queue-name>`
   - `JOB_STORE_BACKEND=dynamodb` (recommended alongside `STORAGE_BACKEND=dynamodb`)

See the root repository `README.md` for local `poll` vs `sqs` behavior.
