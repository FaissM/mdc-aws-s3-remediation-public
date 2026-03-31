# Deploy MDC AWS S3 Block Public Access Remediation
# This script deploys the Logic App and MDC Workflow Automation

param(
    [Parameter(Mandatory=$false)]
    [string]$ResourceGroupName = "mdc-aws-remediation-rg",
    
    [Parameter(Mandatory=$false)]
    [string]$AzureFunctionUrl = "https://mdc-s3-remediation.azurewebsites.net/api/remediate-s3-public-access",
    
    [Parameter(Mandatory=$true)]
    [string]$AzureFunctionApiKey,
    
    [Parameter(Mandatory=$true)]
    [string]$FallbackEmail,
    
    [Parameter(Mandatory=$false)]
    [string]$Location = "swedencentral",
    
    [Parameter(Mandatory=$false)]
    [string]$LogicAppName = "mdc-remediate-s3-block-public-access",
    
    [Parameter(Mandatory=$false)]
    [string]$AutomationName = "remediate-s3-block-public-access"
)

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "MDC AWS Remediation Deployment" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Step 1: Create Resource Group if it doesn't exist
Write-Host "`n[1/4] Checking Resource Group..." -ForegroundColor Yellow
$rg = az group show --name $ResourceGroupName --query name -o tsv 2>$null
if (-not $rg) {
    Write-Host "Creating Resource Group: $ResourceGroupName" -ForegroundColor Green
    az group create --name $ResourceGroupName --location $Location
} else {
    Write-Host "Resource Group exists: $ResourceGroupName" -ForegroundColor Green
}

# Step 2: Deploy Logic App with O365 Connection
Write-Host "`n[2/4] Deploying Logic App and Office 365 Connection..." -ForegroundColor Yellow
$logicAppDeployment = az deployment group create `
    --resource-group $ResourceGroupName `
    --template-file "./arm-templates/logic-app.json" `
    --parameters logicAppName=$LogicAppName azureFunctionUrl=$AzureFunctionUrl azureFunctionApiKey=$AzureFunctionApiKey fallbackEmail=$FallbackEmail location=$Location `
    --query "properties.outputs" `
    -o json | ConvertFrom-Json

$logicAppResourceId = $logicAppDeployment.logicAppResourceId.value
$logicAppUrl = $logicAppDeployment.logicAppUrl.value
$o365ConnectionName = "$LogicAppName-o365"

Write-Host "Logic App deployed: $logicAppResourceId" -ForegroundColor Green

# Step 3: Authorize Office 365 Connection (manual step required)
Write-Host "`n[3/4] Office 365 Connection Authorization Required..." -ForegroundColor Yellow
Write-Host "  1. Go to Azure Portal > Resource Group: $ResourceGroupName" -ForegroundColor White
Write-Host "  2. Find API Connection: $o365ConnectionName" -ForegroundColor White
Write-Host "  3. Click 'Edit API connection' > 'Authorize' > Sign in" -ForegroundColor White
Write-Host "  4. Click 'Save'" -ForegroundColor White
Write-Host ""
Read-Host "Press Enter after authorizing the connection..."

# Step 4: Deploy Workflow Automation
Write-Host "`n[4/4] Deploying MDC Workflow Automation..." -ForegroundColor Yellow
az deployment group create `
    --resource-group $ResourceGroupName `
    --template-file "./arm-templates/workflow-automation.json" `
    --parameters automationName=$AutomationName logicAppResourceId=$logicAppResourceId logicAppTriggerUri=$logicAppUrl location=$Location `
    -o table

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "Deployment Complete!" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "`nResources:"
Write-Host "  - Logic App: $LogicAppName"
Write-Host "  - Workflow Automation: $AutomationName"
Write-Host "`nAzure Function: $AzureFunctionUrl"
Write-Host "Fallback Email: $FallbackEmail"
Write-Host "  4. Test by manually triggering the Logic App"
Write-Host "  5. Monitor runs in Logic App Run History"
