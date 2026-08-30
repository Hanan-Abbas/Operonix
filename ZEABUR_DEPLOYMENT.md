# Zeabur Deployment Guide for Operonix Cloud Backend

This guide explains how to deploy the Operonix cloud backend to Zeabur free tier for the hybrid deployment architecture.

## Prerequisites

- GitHub account with Operonix repository
- Zeabur account (free tier, no credit card required)
- LLM API keys (Groq, OpenRouter, or Gemini)

## Quick Start

### Step 1: Connect GitHub to Zeabur

1. Go to [Zeabur](https://zeabur.com) and sign up with GitHub
2. Authorize Zeabur to access your repositories
3. Navigate to your dashboard

### Step 2: Create New Project

1. Click "Create New Project"
2. Select your Operonix repository from GitHub
3. Zeabur will automatically detect the `zeabur.yaml` configuration file

### Step 3: Configure Environment Variables

1. In your project dashboard, go to "Environment Variables"
2. Add the following variables:

**Required:**
- `GROQ_API_KEY` - Your Groq API key (mark as secret)
- `OPENROUTER_API_KEY` - Your OpenRouter API key (mark as secret)  
- `GEMINI_API_KEY` - Your Gemini API key (mark as secret)

**Optional:**
- `OLLAMA_BASE_URL` - Your Ollama instance URL (if using local LLM)
- `CORS_ORIGINS` - Set to `*` for wildcard or specific domains
- `LOG_LEVEL` - Set to `INFO` for normal logging

**Important**: Mark all API keys as "Secret" to keep them encrypted.

### Step 4: Deploy

1. Click "Deploy" in the Zeabur dashboard
2. Zeabur will automatically:
   - Build the Docker image using `Dockerfile.render`
   - Install dependencies from `requirements.txt`
   - Start the service with `python3 -m api.server`
3. Wait for deployment to complete (usually 2-5 minutes)

### Step 5: Get Your Service URL

1. Once deployed, Zeabur will provide a public URL
2. Copy the URL (e.g., `https://operonix-cloud.zeabur.app`)
3. This is your cloud backend URL for the local agent

## Configuration Details

### zeabur.yaml

The `zeabur.yaml` file configures:

- **Service name**: `operonix-cloud`
- **Dockerfile**: `Dockerfile.render` (lightweight cloud backend)
- **Port**: 8000 (FastAPI default)
- **Health check**: `/health` endpoint
- **Resources**: 512MB RAM, 0.5 CPU (Zeabur free tier)
- **Environment variables**: LLM provider keys and settings

### Dockerfile.render

The `Dockerfile.render` is optimized for Zeabur:

- Uses Python 3.11 slim image
- Installs minimal system dependencies
- Uses `requirements.txt` for Python dependencies
- Excludes desktop automation packages
- Runs as non-root user for security
- Health check built into Dockerfile

## Zeabur Free Tier Features

### Included in Free Tier:
- ✅ 512MB RAM with 4GB burst during builds
- ✅ 0.5 CPU
- ✅ 100GB monthly bandwidth
- ✅ Custom Dockerfile support
- ✅ WebSocket support
- ✅ Static public URL
- ✅ Encrypted environment variables
- ✅ GitHub OAuth authentication
- ✅ No credit card required

### Sleep Behavior:
- Container may sleep during inactivity (similar to Render)
- Wakes automatically on incoming requests
- Your `local_agent.py` reconnection logic handles this seamlessly
- WebSocket connections reconnect automatically

## Running the Local Agent

Once your Zeabur backend is deployed, run the local agent:

```bash
python local_agent.py \
    --session-id your-unique-id \
    --backend-url https://your-zeabur-url.zeabur.app
```

Example:
```bash
python local_agent.py \
    --session-id alex-laptop \
    --backend-url https://operonix-cloud.zeabur.app
```

## Multi-User Setup

For multiple users (friends):

1. **Deploy once** to Zeabur (single backend)
2. **Share with friends**:
   - `local_agent.py` script
   - `local_agent_requirements.txt` dependencies
   - `setup_agent.sh` setup script
   - Your Zeabur service URL
3. **Each friend runs** the agent with their unique session ID

## Troubleshooting

### Deployment Fails

**Build errors:**
- Check Zeabur build logs for specific errors
- Ensure `requirements.txt` is present in repository
- Verify `Dockerfile.render` syntax is correct

**Runtime errors:**
- Check environment variables are set correctly
- Verify API keys are valid and marked as secret
- Check Zeabur service logs for runtime errors

### Connection Issues

**"Waiting for cloud backend to wake up..."**
- This is normal! Zeabur containers may sleep during inactivity
- The agent will automatically retry every 5 seconds
- Wait up to 5 minutes for the backend to wake up

**"Failed to connect after 60 attempts"**
- Check your internet connection
- Verify the Zeabur service URL is correct
- Check Zeabur dashboard to ensure service is running
- Verify environment variables are configured

### WebSocket Issues

**Connections dropping frequently:**
- This may happen if the container sleeps
- Your reconnection logic should handle this automatically
- Check Zeabur logs for sleep/wake patterns

**Friends can't connect:**
- Verify they're using the correct Zeabur URL
- Check their session ID is unique
- Ensure your Zeabur service is running
- Verify environment variables are set correctly

## Monitoring

### Zeabur Dashboard

Monitor your service in the Zeabur dashboard:
- **Resource usage**: CPU, memory, bandwidth
- **Logs**: Real-time application logs
- **Health checks**: Service health status
- **Uptime**: Service availability

### Local Agent Logs

The local agent provides detailed logging:
- Connection attempts and status
- WebSocket connection health
- Command execution results
- Error messages and warnings

## Cost Management

### Free Tier Limits

Zeabur free tier includes:
- 512MB RAM (4GB burst during builds)
- 0.5 CPU
- 100GB monthly bandwidth
- Unlimited deployments

### Exceeding Limits

If you exceed free tier limits:
- Zeabur will pause your service
- No automatic charges
- Service resumes when next billing cycle starts
- You can upgrade to paid tier if needed

## Security Best Practices

1. **Mark API keys as secret** in Zeabur environment variables
2. **Use unique session IDs** for each user
3. **Monitor logs** for suspicious activity
4. **Keep dependencies updated** for security patches
5. **Use HTTPS** (Zeabur provides automatic SSL)

## Migration from Render

If you're migrating from Render:

1. **Update local agent URLs**:
   - Replace Render URL with Zeabur URL
   - No code changes needed otherwise

2. **Environment variables**:
   - Copy API keys from Render to Zeabur
   - Ensure all secrets are marked as secret

3. **Test deployment**:
   - Deploy to Zeabur
   - Test local agent connection
   - Verify WebSocket reconnection
   - Test with multiple users

4. **Update documentation**:
   - Update `HYBRID_DEPLOYMENT.md` with Zeabur URL
   - Share new URL with friends

## Performance Optimization

### Build Performance

Zeabur provides 4GB RAM burst during builds, which helps with:
- Heavy Python dependencies
- Large package installations
- Faster build times

### Runtime Performance

For better runtime performance:
- Monitor memory usage in Zeabur dashboard
- Optimize Python dependencies
- Consider upgrading to paid tier if needed
- Use caching where possible

## Support

- **Zeabur Documentation**: https://zeabur.com/docs
- **Zeabur Discord**: Community support
- **Operonix Issues**: GitHub repository issues

## Summary

Zeabur provides an excellent free tier for Operonix hybrid deployment:
- ✅ Zero cost with generous resources
- ✅ Custom Dockerfile support
- ✅ WebSocket compatibility
- ✅ Better build performance (4GB RAM burst)
- ✅ Easy GitHub integration
- ✅ Your reconnection logic handles sleep behavior

The migration from Render to Zeabur is straightforward with minimal configuration changes.
