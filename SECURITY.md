# Security Policy

## Reporting Security Vulnerabilities

If you discover a security vulnerability in this project, please report it responsibly:

1. **Do NOT** create a public GitHub issue
2. Email the maintainers directly or use GitHub's private vulnerability reporting feature
3. Provide a detailed description of the vulnerability
4. Allow reasonable time for a fix before public disclosure

## Security Best Practices

When deploying this solution:

- **Never commit secrets** - Use Azure Key Vault or environment variables for sensitive values
- **Rotate API keys regularly** - The `MDC_API_KEY` should be rotated periodically
- **Use least-privilege AWS IAM** - Only grant the minimum required S3 permissions
- **Enable audit logging** - Monitor Azure Function and Logic App execution logs
- **Restrict allowed accounts/buckets** - Configure `ALLOWED_ACCOUNT_IDS` and `ALLOWED_BUCKET_NAMES` to limit scope

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x     | :white_check_mark: |

## Acknowledgments

We appreciate responsible disclosure of security issues.
