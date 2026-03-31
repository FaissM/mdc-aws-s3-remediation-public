import azure.functions as func
import logging
import json
import os
import re
import boto3
from botocore.exceptions import ClientError
from functools import lru_cache
from datetime import datetime, timedelta

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# Allowed AWS account IDs (comma-separated in env var)
ALLOWED_ACCOUNT_IDS = os.environ.get('ALLOWED_ACCOUNT_IDS', '').split(',')
# Allowed S3 bucket names (comma-separated in env var)
ALLOWED_BUCKET_NAMES = os.environ.get('ALLOWED_BUCKET_NAMES', '').split(',')

# Cache for AWS credentials (refreshed every 55 minutes)
_aws_credentials_cache = {
    'credentials': None,
    'expiry': None
}

def get_aws_credentials():
    """
    Retrieve AWS credentials from AWS Secrets Manager.
    Credentials are cached for 55 minutes to minimize API calls.
    
    Expected secret format in AWS Secrets Manager:
    {
        "aws_access_key_id": "AKIA...",
        "aws_secret_access_key": "..."
    }
    """
    global _aws_credentials_cache
    
    # Check if we have valid cached credentials
    if (_aws_credentials_cache['credentials'] and 
        _aws_credentials_cache['expiry'] and 
        datetime.utcnow() < _aws_credentials_cache['expiry']):
        logging.debug('Using cached AWS credentials')
        return _aws_credentials_cache['credentials']
    
    secret_arn = os.environ.get('AWS_SECRETS_MANAGER_SECRET_ARN')
    region = os.environ.get('AWS_REGION', 'us-east-1')
    
    if not secret_arn:
        # Fallback to environment variables for local development
        logging.warning('AWS_SECRETS_MANAGER_SECRET_ARN not set, falling back to environment variables')
        return {
            'aws_access_key_id': os.environ.get('AWS_ACCESS_KEY_ID'),
            'aws_secret_access_key': os.environ.get('AWS_SECRET_ACCESS_KEY')
        }
    
    logging.info(f'Retrieving AWS credentials from Secrets Manager: {secret_arn}')
    
    try:
        # Create Secrets Manager client
        # For cross-account access, uses IAM role with secretsmanager:GetSecretValue permission
        secrets_client = boto3.client('secretsmanager', region_name=region)
        
        response = secrets_client.get_secret_value(SecretId=secret_arn)
        secret_data = json.loads(response['SecretString'])
        
        # Cache the credentials for 55 minutes
        _aws_credentials_cache['credentials'] = {
            'aws_access_key_id': secret_data.get('aws_access_key_id'),
            'aws_secret_access_key': secret_data.get('aws_secret_access_key')
        }
        _aws_credentials_cache['expiry'] = datetime.utcnow() + timedelta(minutes=55)
        
        logging.info('Successfully retrieved and cached AWS credentials from Secrets Manager')
        return _aws_credentials_cache['credentials']
        
    except ClientError as e:
        logging.error(f'Failed to retrieve AWS credentials from Secrets Manager: {e}')
        raise

def validate_api_key(req):
    """Validate API key from request header (retrieved from Azure Key Vault)"""
    expected_key = os.environ.get('MDC_API_KEY')
    if not expected_key:
        return False, "API key not configured"
    
    provided_key = req.headers.get('x-api-key')
    if not provided_key or provided_key != expected_key:
        return False, "Invalid or missing API key"
    
    return True, None

def validate_account_id(account_id):
    """Validate AWS account ID format and allowed list"""
    if not account_id:
        return False, "Missing accountId"
    
    if not re.match(r'^\d{12}$', str(account_id)):
        return False, "Invalid accountId format (must be 12 digits)"
    
    if ALLOWED_ACCOUNT_IDS and ALLOWED_ACCOUNT_IDS[0] and account_id not in ALLOWED_ACCOUNT_IDS:
        return False, f"Account {account_id} not in allowed list"
    
    return True, None

def validate_bucket_name(bucket_name):
    """Validate S3 bucket name against allowed list"""
    if not bucket_name:
        return False, "Missing bucketName"
    
    if ALLOWED_BUCKET_NAMES and ALLOWED_BUCKET_NAMES[0] and bucket_name not in ALLOWED_BUCKET_NAMES:
        return False, f"Bucket {bucket_name} not in allowed list"
    
    return True, None

def enable_s3_block_public_access(account_id):
    """Enable S3 Block Public Access at account level"""
    # Get credentials from AWS Secrets Manager
    credentials = get_aws_credentials()
    
    # Create S3 control client with credentials from Secrets Manager
    s3control = boto3.client(
        's3control',
        aws_access_key_id=credentials.get('aws_access_key_id'),
        aws_secret_access_key=credentials.get('aws_secret_access_key'),
        region_name=os.environ.get('AWS_REGION', 'us-east-1')
    )
    
    # Enable all Block Public Access settings
    s3control.put_public_access_block(
        AccountId=account_id,
        PublicAccessBlockConfiguration={
            'BlockPublicAcls': True,
            'IgnorePublicAcls': True,
            'BlockPublicPolicy': True,
            'RestrictPublicBuckets': True
        }
    )
    
    # Verify the settings
    response = s3control.get_public_access_block(AccountId=account_id)
    config = response.get('PublicAccessBlockConfiguration', {})
    
    return {
        'status': 'success',
        'message': f'S3 Block Public Access enabled for account {account_id}',
        'level': 'account',
        'accountId': account_id,
        'configuration': config
    }

def enable_s3_bucket_block_public_access(bucket_name):
    """Enable S3 Block Public Access at bucket level"""
    # Get credentials from AWS Secrets Manager
    credentials = get_aws_credentials()
    
    # Create S3 client with credentials from Secrets Manager
    s3 = boto3.client(
        's3',
        aws_access_key_id=credentials.get('aws_access_key_id'),
        aws_secret_access_key=credentials.get('aws_secret_access_key'),
        region_name=os.environ.get('AWS_REGION', 'us-east-1')
    )
    
    # Enable all Block Public Access settings for the bucket
    s3.put_public_access_block(
        Bucket=bucket_name,
        PublicAccessBlockConfiguration={
            'BlockPublicAcls': True,
            'IgnorePublicAcls': True,
            'BlockPublicPolicy': True,
            'RestrictPublicBuckets': True
        }
    )
    
    # Verify the settings
    response = s3.get_public_access_block(Bucket=bucket_name)
    config = response.get('PublicAccessBlockConfiguration', {})
    
    return {
        'status': 'success',
        'message': f'S3 Block Public Access enabled for bucket {bucket_name}',
        'level': 'bucket',
        'bucketName': bucket_name,
        'configuration': config
    }

@app.route(route="remediate-s3-public-access", methods=["POST"])
def remediate_s3_public_access(req: func.HttpRequest) -> func.HttpResponse:
    """
    Azure Function to remediate S3 Block Public Access.
    Supports both account-level and bucket-level remediation.
    - If bucketName is provided: applies bucket-level settings
    - If only accountId is provided: applies account-level settings
    """
    logging.info('S3 Block Public Access remediation function triggered.')
    
    # Validate API key
    valid, error = validate_api_key(req)
    if not valid:
        logging.warning(f'API key validation failed: {error}')
        return func.HttpResponse(
            json.dumps({'error': error}),
            status_code=401,
            mimetype='application/json'
        )
    
    # Parse request body
    try:
        req_body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({'error': 'Invalid JSON in request body'}),
            status_code=400,
            mimetype='application/json'
        )
    
    account_id = req_body.get('accountId')
    bucket_name = req_body.get('bucketName')
    
    # Determine remediation level
    if bucket_name:
        # Bucket-level remediation
        valid, error = validate_bucket_name(bucket_name)
        if not valid:
            logging.warning(f'Bucket validation failed: {error}')
            return func.HttpResponse(
                json.dumps({'error': error}),
                status_code=403,
                mimetype='application/json'
            )
        
        try:
            result = enable_s3_bucket_block_public_access(bucket_name)
            logging.info(f'Bucket-level remediation successful for {bucket_name}')
            return func.HttpResponse(
                json.dumps(result),
                status_code=200,
                mimetype='application/json'
            )
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            logging.error(f'AWS API error: {error_code} - {error_message}')
            return func.HttpResponse(
                json.dumps({'error': f'AWS API error: {error_message}', 'code': error_code}),
                status_code=500,
                mimetype='application/json'
            )
    else:
        # Account-level remediation
        valid, error = validate_account_id(account_id)
        if not valid:
            logging.warning(f'Account validation failed: {error}')
            return func.HttpResponse(
                json.dumps({'error': error}),
                status_code=403,
                mimetype='application/json'
            )
        
        try:
            result = enable_s3_block_public_access(account_id)
            logging.info(f'Account-level remediation successful for {account_id}')
            return func.HttpResponse(
                json.dumps(result),
                status_code=200,
                mimetype='application/json'
            )
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            logging.error(f'AWS API error: {error_code} - {error_message}')
            return func.HttpResponse(
                json.dumps({'error': f'AWS API error: {error_message}', 'code': error_code}),
                status_code=500,
                mimetype='application/json'
            )
        except Exception as e:
            logging.error(f'Unexpected error: {str(e)}')
            return func.HttpResponse(
                json.dumps({'error': str(e)}),
                status_code=500,
                mimetype='application/json'
            )
