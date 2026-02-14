#!/bin/bash
set -euo pipefail

# ============================================================
# Alert Mailer - Multi-Stack Deploy Script
# Usage: ./scripts/deploy.sh <ENV>
#   ENV: dev | stg | prod
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
CFN_DIR="${PROJECT_DIR}/cloudformation"
TEMPLATES_DIR="${CFN_DIR}/templates"
PARAMS_DIR="${CFN_DIR}/parameters"
LAMBDA_DIR="${PROJECT_DIR}/lambda"

REGION="${AWS_REGION:-ap-northeast-1}"

# --- Argument validation ---
ENV="${1:-}"
if [[ -z "${ENV}" ]] || [[ ! "${ENV}" =~ ^(dev|stg|prod)$ ]]; then
  echo "Usage: $0 <ENV>"
  echo "  ENV: dev | stg | prod"
  exit 1
fi

PARAMS_FILE="${PARAMS_DIR}/${ENV}.json"
if [[ ! -f "${PARAMS_FILE}" ]]; then
  echo "ERROR: Parameter file not found: ${PARAMS_FILE}"
  exit 1
fi

# --- Helper: extract parameter value from JSON ---
get_param() {
  local key="$1"
  python3 -c "
import json, sys
with open('${PARAMS_FILE}', encoding='utf-8') as f:
    params = json.load(f)
for p in params:
    if p['ParameterKey'] == '${key}':
        print(p['ParameterValue'])
        sys.exit(0)
print('')
"
}

S3_BUCKET=$(get_param "LambdaCodeS3Bucket")
STACK_PREFIX="${ENV}-alert-mailer"

echo "============================================"
echo "  Alert Mailer Deploy: ${ENV}"
echo "  Region: ${REGION}"
echo "  S3 Bucket: ${S3_BUCKET}"
echo "  Stack prefix: ${STACK_PREFIX}"
echo "============================================"

# --- Step 1: Package Lambda code ---
echo ""
echo "[1/4] Packaging Lambda code..."

cd "${LAMBDA_DIR}/layers/lambda_common"
zip -r "${PROJECT_DIR}/lambda-common-layer.zip" python/
cd "${PROJECT_DIR}"

cd "${LAMBDA_DIR}/alertmailer"
zip -r "${PROJECT_DIR}/alert-mailer.zip" .
cd "${PROJECT_DIR}"

# --- Step 2: Upload to S3 ---
echo ""
echo "[2/4] Uploading to S3..."

ALERTMAILER_S3_KEY=$(get_param "AlertMailerCodeS3Key")
LAYER_S3_KEY=$(get_param "CommonLayerS3Key")

aws s3 cp lambda-common-layer.zip "s3://${S3_BUCKET}/${LAYER_S3_KEY}" --region "${REGION}"
aws s3 cp alert-mailer.zip "s3://${S3_BUCKET}/${ALERTMAILER_S3_KEY}" --region "${REGION}"

rm -f lambda-common-layer.zip alert-mailer.zip

# --- Step 3: Deploy stacks (dependency order) ---
echo ""
echo "[3/4] Deploying CloudFormation stacks..."

deploy_stack() {
  local component="$1"
  local template="${TEMPLATES_DIR}/${component}.yaml"
  local stack_name="${STACK_PREFIX}-${component}"

  echo "  -> Deploying: ${stack_name}"

  # Extract only parameters that the template accepts
  local template_params
  template_params=$(aws cloudformation validate-template \
    --template-body "file://${template}" \
    --region "${REGION}" \
    --query "Parameters[].ParameterKey" \
    --output text 2>/dev/null || echo "")

  local overrides=""
  if [[ -n "${template_params}" ]]; then
    for key in ${template_params}; do
      local value
      value=$(get_param "${key}")
      if [[ -n "${value}" ]]; then
        overrides="${overrides} ${key}=${value}"
      fi
    done
  fi

  aws cloudformation deploy \
    --template-file "${template}" \
    --stack-name "${stack_name}" \
    --region "${REGION}" \
    --capabilities CAPABILITY_NAMED_IAM \
    --parameter-overrides ${overrides} \
    --no-fail-on-empty-changeset

  echo "  -> Done: ${stack_name}"
}

# Deploy Order (see cloudformation/stacks/README.md):
# 1. sqs + observability (parallel)
# 2. iam
# 3. lambda + api-gateway (parallel)

echo "  Phase 1: sqs, observability"
deploy_stack "sqs"
deploy_stack "observability"

echo "  Phase 2: iam"
deploy_stack "iam"

echo "  Phase 3: lambda, api-gateway"
deploy_stack "lambda"
deploy_stack "api-gateway"

# --- Step 4: Output results ---
echo ""
echo "[4/4] Deploy complete!"
echo ""
echo "Stack outputs:"
for component in sqs iam lambda api-gateway observability; do
  echo "  --- ${STACK_PREFIX}-${component} ---"
  aws cloudformation describe-stacks \
    --stack-name "${STACK_PREFIX}-${component}" \
    --region "${REGION}" \
    --query "Stacks[0].Outputs[].[OutputKey, OutputValue]" \
    --output table 2>/dev/null || echo "  (no outputs)"
done
