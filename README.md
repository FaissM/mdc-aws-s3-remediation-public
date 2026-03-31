# MDC AWS S3 Block Public Access Remediation

Automatically remediate AWS S3 public access misconfigurations detected by Microsoft Defender for Cloud (MDC).

**Supported Recommendations:**
- "S3 Block Public Access setting should be enabled" (account-level)
- "S3 Block Public Access setting should be enabled at the bucket level" (bucket-level)

---

## Table of Contents

1. [How It Works](#how-it-works)
2. [Prerequisites](#prerequisites)
3. [Quick Start](#quick-start)
4. [Step-by-Step Deployment](#step-by-step-deployment)
5. [Configuration](#configuration)
6. [Testing](#testing)
7. [Security Controls](#security-controls)
8. [Troubleshooting](#troubleshooting)
9. [Extending to Other Recommendations](#extending-to-other-aws-recommendations)
10. [File Reference](#file-reference)

---

## How It Works

When MDC detects an S3 bucket or account with Block Public Access disabled, the following automated workflow executes:

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     MICROSOFT DEFENDER FOR CLOUD                            │
│  Scans AWS → Finds unhealthy S3 Block Public Access recommendation          │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│              WORKFLOW AUTOMATION (workflow-automation.json)                  │
│                                                                              │
│  Two automations:                                                            │
│  1. remediate-s3-block-public-access-account                                │
│     └─ Triggers when: "S3 Block Public Access setting should be enabled"   │
│                                                                              │
│  2. remediate-s3-block-public-access-bucket                                 │
│     └─ Triggers when: "...at the bucket level"                              │
│     └─ FILTER: resourceId contains "mywsbucketmdc"                          │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      LOGIC APP (logic-app.json)                              │
│                                                                              │
│  1. Parse MDC Alert                                                          │
│  2. Query MDC Governance Rules (via managed identity)                        │
│  3. Extract owner: Governance Rules → MDC assigned → Fallback email          │
│  4. Extract: AWS Account ID, Bucket Name (if bucket-level)                   │
│  5. Call Azure Function with { accountId, bucketName }                       │
│  6. Send email notification via Office 365                                   │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                    AZURE FUNCTION (function_app.py)                          │
│                                                                              │
│  1. Validate API key (x-api-key header)                                      │
│  2. Validate against allowlists (ALLOWED_ACCOUNT_IDS, ALLOWED_BUCKET_NAMES) │
│  3. Determine level:                                                         │
│     - If bucketName → s3.put_public_access_block(Bucket=...)                │
│     - If accountId  → s3control.put_public_access_block(AccountId=...)      │
│  4. Return configuration evidence                                            │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AWS S3                                          │
│  All 4 Block Public Access settings enabled:                                │
│  ✅ BlockPublicAcls  ✅ IgnorePublicAcls  ✅ BlockPublicPolicy  ✅ RestrictPublicBuckets │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Owner Resolution Priority:**
1. Assigned owner from MDC Governance Rules
2. Owner from MDC alert data (assigned owner)
3. Owner from MDC metadata
4. Fallback email parameter

**MDC Scan Frequency:** MDC scans AWS resources approximately every 12 hours. After remediation, expect the next scan to show the resource as "Healthy."

---

## Prerequisites

Before you begin, ensure you have:

| Requirement | Details |
|-------------|---------|
| **Azure Subscription** | With Microsoft Defender for Cloud enabled |
| **Azure CLI** | Installed and logged in (`az login`) |
| **Azure Functions Core Tools** | For deploying the Python function (`npm install -g azure-functions-core-tools@4`) || **Azure Key Vault** | For secure storage of API keys and AWS credentials || **AWS IAM User** | With programmatic access (Access Key + Secret Key) |
| **AWS IAM Permissions** | `s3:PutAccountPublicAccessBlock`, `s3:GetAccountPublicAccessBlock`, `s3:PutPublicAccessBlock`, `s3:GetPublicAccessBlock` |
| **Office 365 Account** | For sending email notifications |
| **AWS Connector in MDC** | Your AWS account connected to Defender for Cloud |

---

## Quick Start

If you're familiar with Azure deployments, here's the fast path:

```powershell
# 1. Deploy Azure Function
cd azure-function
func azure functionapp publish mdc-s3-remediation --python

# 2. Create Key Vault and store secrets
$kvName = "mdc-s3-kv-$(Get-Random -Maximum 9999)"
az keyvault create --name $kvName --resource-group mdc-aws-remediation-rg --location swedencentral --enable-rbac-authorization true

# Assign Key Vault Secrets Officer to yourself
$userId = az ad signed-in-user show --query "id" -o tsv
$kvId = az keyvault show --name $kvName --query "id" -o tsv
az role assignment create --role "Key Vault Secrets Officer" --assignee-object-id $userId --scope $kvId

# Store secrets
az keyvault secret set --vault-name $kvName --name "mdc-api-key" --value "<generate-a-secure-key>"
az keyvault secret set --vault-name $kvName --name "aws-access-key-id" --value "<your-aws-access-key>"
az keyvault secret set --vault-name $kvName --name "aws-secret-access-key" --value "<your-aws-secret-key>"

# 3. Enable managed identity and grant Key Vault access
az functionapp identity assign --name mdc-s3-remediation --resource-group mdc-aws-remediation-rg
$principalId = az functionapp show --name mdc-s3-remediation --resource-group mdc-aws-remediation-rg --query "identity.principalId" -o tsv
az role assignment create --role "Key Vault Secrets User" --assignee-object-id $principalId --assignee-principal-type ServicePrincipal --scope $kvId

# 4. Configure Function App with Key Vault references
az functionapp config appsettings set --name mdc-s3-remediation --resource-group mdc-aws-remediation-rg --settings `
  "MDC_API_KEY=@Microsoft.KeyVault(SecretUri=https://$kvName.vault.azure.net/secrets/mdc-api-key)" `
  "AWS_ACCESS_KEY_ID=@Microsoft.KeyVault(SecretUri=https://$kvName.vault.azure.net/secrets/aws-access-key-id)" `
  "AWS_SECRET_ACCESS_KEY=@Microsoft.KeyVault(SecretUri=https://$kvName.vault.azure.net/secrets/aws-secret-access-key)" `
  AWS_REGION=eu-north-1 `
  ALLOWED_ACCOUNT_IDS=<your-aws-account-id> `
  ALLOWED_BUCKET_NAMES=<your-bucket-name>

# 5. Deploy Logic App + Workflow Automations
./deploy.ps1 -AzureFunctionApiKey "<your-mdc-api-key>" -FallbackEmail "security@yourcompany.com"

# 6. Authorize Office 365 connection in Azure Portal
#    Portal → Resource Group → API Connections → mdc-remediate-s3-block-public-access-o365 → Authorize
```

---

## Step-by-Step Deployment

### Step 1: Create the Azure Function App (if not exists)

If you don't have a Function App yet, create one:

```powershell
$rg = "mdc-aws-remediation-rg"
$location = "swedencentral"
$funcApp = "mdc-s3-remediation"
$storage = "mdcs3remediationstore"

# Create resource group
az group create --name $rg --location $location

# Create storage account
az storage account create --name $storage --resource-group $rg --location $location --sku Standard_LRS

# Create Function App
az functionapp create --name $funcApp --resource-group $rg --storage-account $storage `
  --consumption-plan-location $location --runtime python --runtime-version 3.11 --functions-version 4
```

### Step 2: Deploy the Azure Function Code

```powershell
cd azure-function
func azure functionapp publish mdc-s3-remediation --python
```

### Step 3: Create Azure Key Vault and Store Secrets

Secrets are stored in Azure Key Vault for security:

```powershell
# Create Key Vault with RBAC authorization
$kvName = "mdc-s3-kv-$(Get-Random -Maximum 9999)"
az keyvault create --name $kvName --resource-group mdc-aws-remediation-rg --location swedencentral --enable-rbac-authorization true

# Grant yourself access to manage secrets
$userId = az ad signed-in-user show --query "id" -o tsv
$kvId = az keyvault show --name $kvName --query "id" -o tsv
az role assignment create --role "Key Vault Secrets Officer" --assignee-object-id $userId --scope $kvId

# Wait for RBAC propagation
Start-Sleep -Seconds 10

# Store secrets
az keyvault secret set --vault-name $kvName --name "mdc-api-key" --value "<generate-a-secure-key>"
az keyvault secret set --vault-name $kvName --name "aws-access-key-id" --value "<your-aws-access-key>"
az keyvault secret set --vault-name $kvName --name "aws-secret-access-key" --value "<your-aws-secret-key>"
```

### Step 4: Configure Function App with Managed Identity

Enable managed identity and grant Key Vault access:

```powershell
# Enable system-assigned managed identity
az functionapp identity assign --name mdc-s3-remediation --resource-group mdc-aws-remediation-rg

# Grant Function App access to read secrets
$principalId = az functionapp show --name mdc-s3-remediation --resource-group mdc-aws-remediation-rg --query "identity.principalId" -o tsv
az role assignment create --role "Key Vault Secrets User" --assignee-object-id $principalId --assignee-principal-type ServicePrincipal --scope $kvId
```

### Step 5: Configure Environment Variables

Set the Function App settings with Key Vault references:

```powershell
az functionapp config appsettings set --name mdc-s3-remediation --resource-group mdc-aws-remediation-rg --settings `
  "MDC_API_KEY=@Microsoft.KeyVault(SecretUri=https://$kvName.vault.azure.net/secrets/mdc-api-key)" `
  "AWS_ACCESS_KEY_ID=@Microsoft.KeyVault(SecretUri=https://$kvName.vault.azure.net/secrets/aws-access-key-id)" `
  "AWS_SECRET_ACCESS_KEY=@Microsoft.KeyVault(SecretUri=https://$kvName.vault.azure.net/secrets/aws-secret-access-key)" `
  AWS_REGION=eu-north-1 `
  ALLOWED_ACCOUNT_IDS=<your-aws-account-id> `
  ALLOWED_BUCKET_NAMES=<your-bucket-name>
```

| Setting | Description | Storage |
|---------|-------------|---------|
| `MDC_API_KEY` | API key to protect the function endpoint | Key Vault reference |
| `AWS_ACCESS_KEY_ID` | AWS IAM access key | Key Vault reference |
| `AWS_SECRET_ACCESS_KEY` | AWS IAM secret key | Key Vault reference |
| `AWS_REGION` | AWS region for S3 control operations | Plain text |
| `ALLOWED_ACCOUNT_IDS` | Comma-separated AWS account IDs allowed | Plain text |
| `ALLOWED_BUCKET_NAMES` | Comma-separated bucket names allowed | Plain text |

### Step 6: Deploy Logic App and Workflow Automations

Run the deployment script:

```powershell
./deploy.ps1 -AzureFunctionApiKey "<your-mdc-api-key>" -FallbackEmail "security@yourcompany.com"
```

This creates:
- **Logic App:** Orchestrates the remediation workflow
- **Workflow Automation (account-level):** Triggers for account-level recommendations
- **Workflow Automation (bucket-level):** Triggers for bucket-level recommendations (filtered to your target bucket)

### Step 7: Authorize Office 365 Connection

The Logic App needs permission to send emails:

1. Go to **Azure Portal**
2. Navigate to your **Resource Group** (`mdc-aws-remediation-rg`)
3. Click **API Connections** → `mdc-remediate-s3-block-public-access-o365`
4. Click **Edit API connection**
5. Click **Authorize** → Sign in with your Office 365 account
6. Click **Save**

✅ **Deployment complete!** The automation will now trigger whenever MDC detects an unhealthy S3 Block Public Access recommendation.

---

## Configuration

### Changing the Target Bucket

To remediate a different bucket, update **two places**:

#### 1. Workflow Automation Filter

Edit [arm-templates/workflow-automation.json](arm-templates/workflow-automation.json) line 119:

```json
{
  "propertyJPath": "properties.resourceDetails.Id",
  "expectedValue": "your-new-bucket-name",
  "operator": "Contains"
}
```

Then redeploy:
```powershell
./deploy.ps1 -AzureFunctionApiKey "<your-api-key>" -FallbackEmail "security@yourcompany.com"
```

#### 2. Azure Function Allowlist

Update the Function App setting:
```powershell
az functionapp config appsettings set --name mdc-s3-remediation --resource-group mdc-aws-remediation-rg `
  --settings ALLOWED_BUCKET_NAMES=your-new-bucket-name
```

### Adding Multiple Buckets

Use comma-separated values:
```powershell
az functionapp config appsettings set --name mdc-s3-remediation --resource-group mdc-aws-remediation-rg `
  --settings ALLOWED_BUCKET_NAMES=bucket1,bucket2,bucket3
```

---

## Testing

### Test the Azure Function Directly

**Account-level remediation:**
```bash
curl -X POST "https://mdc-s3-remediation.azurewebsites.net/api/remediate-s3-public-access" \
  -H "Content-Type: application/json" \
  -H "x-api-key: <your-api-key>" \
  -d '{"accountId":"624725955754"}'
```

**Bucket-level remediation:**
```bash
curl -X POST "https://mdc-s3-remediation.azurewebsites.net/api/remediate-s3-public-access" \
  -H "Content-Type: application/json" \
  -H "x-api-key: <your-api-key>" \
  -d '{"bucketName":"mywsbucketmdc"}'
```

### Test the Logic App Manually

1. Go to **Logic App** → **Run Trigger** → **When_a_MDC_recommendation_is_triggered**
2. Paste this sample payload in the **Body** field:

```json
{
  "schemaId": "test-manual-run",
  "data": {
    "properties": {
      "displayName": "S3 Block Public Access setting should be enabled at the bucket level",
      "status": {
        "code": "Unhealthy"
      },
      "resourceDetails": {
        "Source": "Aws",
        "Id": "arn:aws:s3:::mywsbucketmdc",
        "ConnectorId": "624725955754",
        "Region": "eu-north-1"
      }
    }
  }
}
```

3. Click **Run**

### End-to-End Test

1. **Disable** Block Public Access on your S3 bucket in AWS Console
2. **Wait** for MDC to scan (up to 12 hours) or trigger the Logic App manually
3. **Verify** Block Public Access is re-enabled in AWS Console
4. **Check** your email for the notification

---

## Security Controls

Multiple layers of security protect this automation:

| Layer | Control | How It Works |
|-------|---------|--------------|
| **Azure Key Vault** | Secrets management | All sensitive credentials (API keys, AWS keys) stored securely in Key Vault |
| **Azure Key Vault** | Key Vault references | Function App retrieves secrets at runtime via `@Microsoft.KeyVault()` syntax |
| **Azure Function** | System-assigned managed identity | Authenticates to Key Vault without stored credentials |
| **Logic App** | System-assigned managed identity | Authenticates to Azure APIs without stored credentials |
| **Logic App** | Governance rules lookup | Queries MDC Governance API to find resource owner |
| **Logic App** | Owner resolution priority | Uses cascade: Governance Rules → MDC assigned → Fallback email |
| **Workflow Automation** | Bucket filter | Only triggers for specific bucket names (prevents remediation of unintended buckets) |
| **Azure Function** | API key validation | Requests without valid `x-api-key` header are rejected with 401 |
| **Azure Function** | Account allowlist | Only processes remediation for pre-approved AWS account IDs |
| **Azure Function** | Bucket allowlist | Only processes remediation for pre-approved bucket names |
| **AWS IAM** | Least privilege | IAM user has only the minimum S3 permissions required |

---

## Troubleshooting

### Logic App Fails with "Parse_MDC_Alert" Error

**Symptom:** `Required property 'content' expects a value but got null`

**Cause:** You ran the Logic App manually without providing a request body.

**Solution:** Use the "Run with payload" option and paste a sample JSON body (see [Testing](#test-the-logic-app-manually)).

### Function Returns 401 Unauthorized

**Cause:** Missing or incorrect API key.

**Solution:** Ensure you're passing the correct `x-api-key` header that matches `MDC_API_KEY` in Function App settings.

### Function Returns 403 Forbidden

**Cause:** Account ID or bucket name not in allowlist.

**Solution:** Add the account/bucket to `ALLOWED_ACCOUNT_IDS` or `ALLOWED_BUCKET_NAMES` environment variables.

### Email Not Sent

**Cause:** Office 365 connection not authorized.

**Solution:** Re-authorize the API connection (see [Step 5](#step-5-authorize-office-365-connection)).

### Automation Not Triggering

**Cause:** MDC hasn't scanned yet, or bucket doesn't match the filter.

**Solution:**
- MDC scans every ~12 hours. Wait for the next scan cycle.
- Verify the bucket name in workflow automation matches your bucket.
- Check that the recommendation status is "Unhealthy" in MDC.

---

## Extending to Other AWS Recommendations

This framework can be extended to remediate other AWS security recommendations.

### Step 1: Identify the Recommendation

Find the exact recommendation name in MDC:
```powershell
az security assessment list --query "[?contains(displayName, '<keyword>') && properties.resourceDetails.Source=='Aws'].displayName" -o tsv
```

**Example recommendations:**
- `S3 buckets should require requests to use Secure Socket Layer`
- `Ensure MFA Delete is enabled on S3 buckets`
- `CloudTrail should be enabled`

### Step 2: Add Workflow Automation

In `arm-templates/workflow-automation.json`, add a new automation resource:

```json
{
  "type": "Microsoft.Security/automations",
  "name": "[concat(parameters('automationName'), '-<new-recommendation>')]",
  "properties": {
    "description": "Remediate <new recommendation>",
    "isEnabled": true,
    "sources": [{
      "eventSource": "Assessments",
      "ruleSets": [{
        "rules": [
          {
            "propertyJPath": "properties.displayName",
            "expectedValue": "<EXACT recommendation name from MDC>",
            "operator": "Equals"
          },
          {
            "propertyJPath": "properties.status.code",
            "expectedValue": "Unhealthy",
            "operator": "Equals"
          },
          {
            "propertyJPath": "properties.resourceDetails.Source",
            "expectedValue": "Aws",
            "operator": "Equals"
          }
        ]
      }]
    }],
    "actions": [{ "actionType": "LogicApp", ... }]
  }
}
```

### Step 3: Add Remediation Logic

In `azure-function/function_app.py`, add a new function:

```python
def enable_<new_feature>(resource_id):
    """Enable <feature> for AWS resource"""
    client = boto3.client('<aws-service>')
    client.<api_call>(...)
    return {'status': 'success', 'message': '...'}

@app.route(route="remediate-<new-feature>", methods=["POST"])
def remediate_new_feature(req: func.HttpRequest) -> func.HttpResponse:
    # Validate API key
    # Parse request
    # Call remediation function
    # Return result
```

### Step 4: Add AWS IAM Permissions

Grant the IAM user permissions for the new API calls:
```bash
aws iam put-user-policy --user-name mdc-remediation-user \
  --policy-name <NewPolicy> \
  --policy-document file://new-policy.json
```

### Step 5: Deploy and Test

```powershell
# Deploy updated function
cd azure-function
func azure functionapp publish mdc-s3-remediation --python

# Deploy updated workflow automation
./deploy.ps1 -AzureFunctionApiKey "<your-api-key>" -FallbackEmail "security@yourcompany.com"
```

### Example: Adding SSL/TLS Enforcement for S3

| Step | Action |
|------|--------|
| 1 | Recommendation: `S3 buckets should require requests to use Secure Socket Layer` |
| 2 | Add automation with `expectedValue` = exact recommendation name |
| 3 | Add `enable_s3_ssl_policy(bucket_name)` function using `s3.put_bucket_policy()` |
| 4 | Extract bucket name from `resourceDetails.Id` |
| 5 | Add IAM permission: `s3:PutBucketPolicy` |
| 6 | Deploy and test |

---

## File Reference

| File | Purpose |
|------|---------|
| `deploy.ps1` | PowerShell script that deploys both the Logic App and Workflow Automations to Azure using ARM templates. Run this after deploying the Azure Function. |
| `arm-templates/function-app.json` | ARM template defining the Azure Function App infrastructure. Uses managed identity instead of storage account keys. Creates the Function App, App Service Plan, Storage Account, and Application Insights. |
| `arm-templates/logic-app.json` | ARM template defining the Logic App workflow. Orchestrates the remediation flow: receives MDC webhook → parses alert data → extracts AWS account/bucket info → calls Azure Function → sends email notification via Office 365. |
| `arm-templates/workflow-automation.json` | ARM template defining MDC Workflow Automations. Creates two automations that monitor MDC assessments and trigger the Logic App when S3 Block Public Access recommendations become unhealthy. |
| `azure-function/function_app.py` | Python code containing the remediation logic. Validates API key and allowlists, then calls AWS boto3 APIs to enable all 4 Block Public Access settings. Supports credential retrieval from environment variables (via Key Vault references). |
| `azure-function/host.json` | Azure Functions runtime configuration. Required by the Functions runtime to configure logging, extensions, and HTTP settings. |
| `azure-function/local.settings.json` | Environment variables for local development only. In production, secrets are stored in **Azure Key Vault** and referenced using `@Microsoft.KeyVault()` syntax. |
| `azure-function/requirements.txt` | Python package dependencies. Lists `azure-functions` (Functions SDK) and `boto3` (AWS SDK). Azure installs these automatically during deployment. |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           AZURE RESOURCES                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────┐    ┌─────────────────────┐                         │
│  │   Azure Key Vault   │    │   Workflow          │                         │
│  │                     │    │   Automations       │                         │
│  │  • mdc-api-key      │    │                     │                         │
│  │  • aws-access-key-id│    │  Trigger on MDC     │                         │
│  │  • aws-secret-...   │    │  recommendations    │                         │
│  └──────────┬──────────┘    └──────────┬──────────┘                         │
│             │                          │                                     │
│             │ @Microsoft.KeyVault()    │                                     │
│             ▼                          ▼                                     │
│  ┌─────────────────────┐    ┌─────────────────────┐                         │
│  │   Azure Function    │◄───│     Logic App       │                         │
│  │                     │    │                     │                         │
│  │  System Managed     │    │  System Managed     │                         │
│  │  Identity           │    │  Identity           │                         │
│  └──────────┬──────────┘    └─────────────────────┘                         │
│             │                                                                │
└─────────────┼────────────────────────────────────────────────────────────────┘
              │
              │ AWS API Calls (boto3)
              ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AWS RESOURCES                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────┐    ┌─────────────────────┐                         │
│  │    IAM User         │    │   S3 Buckets        │                         │
│  │                     │    │                     │                         │
│  │  Least privilege:   │───▶│  Block Public       │                         │
│  │  s3:Put/Get...      │    │  Access enabled     │                         │
│  └─────────────────────┘    └─────────────────────┘                         │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```
