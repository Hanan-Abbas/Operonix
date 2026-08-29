#!/bin/bash

# =============================================================================
# Operonix AWS Deployment Script (App Runner - Free Tier)
# =============================================================================

set -euo pipefail

# Color helpers
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

ok()   { echo -e "${GREEN}  ✓ $*${NC}"; }
warn() { echo -e "${YELLOW}  ⚠ $*${NC}"; }
err()  { echo -e "${RED}  ✗ $*${NC}"; }
info() { echo -e "${CYAN}  → $*${NC}"; }
hdr()  { echo -e "\n${BOLD}$*${NC}"; }

# Configuration
AWS_REGION="${AWS_REGION:-us-east-1}"
SERVICE_NAME="operonix"
REPO_NAME="operonix-repo"

# Check prerequisites
check_prerequisites() {
    hdr "━━━ Checking Prerequisites ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # Check AWS CLI
    if ! command -v aws &> /dev/null; then
        err "AWS CLI not found. Install from: https://aws.amazon.com/cli/"
        exit 1
    fi
    ok "AWS CLI found"
    
    # Check Docker
    if ! command -v docker &> /dev/null; then
        err "Docker not found. Install from: https://docs.docker.com/get-docker/"
        exit 1
    fi
    ok "Docker found"
    
    # Check AWS credentials
    if ! aws sts get-caller-identity &> /dev/null; then
        err "AWS credentials not configured. Run: aws configure"
        exit 1
    fi
    ok "AWS credentials configured"
    
    # Check if user is logged in to ECR
    if ! aws ecr get-login-password --region "$AWS_REGION" &> /dev/null; then
        warn "ECR login may be required during deployment"
    fi
}

# Build Docker image
build_image() {
    hdr "━━━ Building Docker Image ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    info "Building Operonix Docker image..."
    docker build -t "$SERVICE_NAME:latest" .
    ok "Docker image built successfully"
}

# Create ECR repository
create_ecr_repo() {
    hdr "━━━ Creating ECR Repository ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    if aws ecr describe-repositories --repository-names "$REPO_NAME" --region "$AWS_REGION" &> /dev/null; then
        info "Repository $REPO_NAME already exists"
    else
        info "Creating ECR repository: $REPO_NAME"
        aws ecr create-repository \
            --repository-name "$REPO_NAME" \
            --region "$AWS_REGION" \
            --image-scanning-configuration scanOnPush=true
        ok "ECR repository created"
    fi
    
    # Get repository URI
    REPO_URI=$(aws ecr describe-repositories \
        --repository-names "$REPO_NAME" \
        --region "$AWS_REGION" \
        --query 'repositories[0].repositoryUri' \
        --output text)
    
    ok "Repository URI: $REPO_URI"
}

# Login to ECR
login_ecr() {
    hdr "━━━ Logging in to ECR ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    info "Logging in to Amazon ECR..."
    aws ecr get-login-password --region "$AWS_REGION" | \
        docker login --username AWS --password-stdin "$REPO_URI"
    ok "Logged in to ECR"
}

# Push image to ECR
push_image() {
    hdr "━━━ Pushing Image to ECR ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    info "Tagging image for ECR..."
    docker tag "$SERVICE_NAME:latest" "$REPO_URI:latest"
    
    info "Pushing image to ECR..."
    docker push "$REPO_URI:latest"
    ok "Image pushed to ECR"
}

# Create IAM role for App Runner
create_iam_role() {
    hdr "━━━ Creating IAM Role ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    ROLE_NAME="AppRunner-Operonix-Role"
    
    if aws iam get-role --role-name "$ROLE_NAME" &> /dev/null; then
        info "IAM role $ROLE_NAME already exists"
        ROLE_ARN=$(aws iam get-role --role-name "$ROLE_NAME" --query 'Role.Arn' --output text)
    else
        info "Creating IAM role: $ROLE_NAME"
        
        # Create trust policy
        cat > /tmp/trust-policy.json <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": {
        "Service": "apprunner.amazonaws.com"
      },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF
        
        # Create role
        ROLE_ARN=$(aws iam create-role \
            --role-name "$ROLE_NAME" \
            --assume-role-policy-document file:///tmp/trust-policy.json \
            --query 'Role.Arn' \
            --output text)
        
        # Attach App Runner service policy
        aws iam attach-role-policy \
            --role-name "$ROLE_NAME" \
            --policy-arn arn:aws:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess
        
        ok "IAM role created"
    fi
    
    ok "Role ARN: $ROLE_ARN"
}

# Deploy to App Runner
deploy_apprunner() {
    hdr "━━━ Deploying to App Runner ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # Update service configuration with actual values
    sed "s|IMAGE_URI_PLACEHOLDER|${REPO_URI}:latest|g" aws/apprunner-service.json > /tmp/apprunner-config.json
    sed -i "s|IAM_ROLE_ARN_PLACEHOLDER|${ROLE_ARN}|g" /tmp/apprunner-config.json
    
    info "Creating App Runner service..."
    
    # Check if service already exists
    if aws apprunner describe-service --service-arn "arn:aws:apprunner:${AWS_REGION}:$(aws sts get-caller-identity --query Account --output text):service/${SERVICE_NAME}" --region "$AWS_REGION" &> /dev/null; then
        info "Service already exists, updating deployment..."
        aws apprunner update-service \
            --service-arn "arn:aws:apprunner:${AWS_REGION}:$(aws sts get-caller-identity --query Account --output text):service/${SERVICE_NAME}" \
            --source-configuration file:///tmp/apprunner-config.json \
            --region "$AWS_REGION"
    else
        info "Creating new App Runner service..."
        aws apprunner create-service \
            --service-name "$SERVICE_NAME" \
            --source-configuration file:///tmp/apprunner-config.json \
            --region "$AWS_REGION"
    fi
    
    ok "App Runner service deployed"
}

# Get service URL
get_service_url() {
    hdr "━━━ Service Information ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    info "Waiting for service to become active..."
    
    # Wait for service to be active (max 10 minutes)
    for i in {1..20}; do
        STATUS=$(aws apprunner describe-service \
            --service-arn "arn:aws:apprunner:${AWS_REGION}:$(aws sts get-caller-identity --query Account --output text):service/${SERVICE_NAME}" \
            --region "$AWS_REGION" \
            --query 'Service.Status' \
            --output text)
        
        if [ "$STATUS" = "RUNNING" ]; then
            SERVICE_URL=$(aws apprunner describe-service \
                --service-arn "arn:aws:apprunner:${AWS_REGION}:$(aws sts get-caller-identity --query Account --output text):service/${SERVICE_NAME}" \
                --region "$AWS_REGION" \
                --query 'Service.ServiceUrl' \
                --output text)
            
            ok "Service is running!"
            ok "Service URL: https://${SERVICE_URL}"
            ok "Health check: https://${SERVICE_URL}/health"
            return 0
        fi
        
        info "Current status: $STATUS (waiting...)"
        sleep 30
    done
    
    warn "Service deployment is taking longer than expected. Check the AWS Console."
}

# Main execution
main() {
    hdr "━━━ Operonix AWS Deployment (App Runner Free Tier) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    check_prerequisites
    build_image
    create_ecr_repo
    login_ecr
    push_image
    create_iam_role
    deploy_apprunner
    get_service_url
    
    hdr "━━━ Deployment Complete ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    ok "Operonix has been deployed to AWS App Runner (Free Tier)"
    info "Monitor your service at: https://console.aws.amazon.com/apprunner/"
}

# Run main function
main
