#!/bin/bash
# Test script to validate docker-compose.yml has all required services

set -e

SERVICES=$(docker compose config --services)
REQUIRED_SERVICES=("frontend" "backend" "worker")

echo "Found services: $SERVICES"

for svc in "${REQUIRED_SERVICES[@]}"; do
    if echo "$SERVICES" | grep -q "^${svc}$"; then
        echo "✓ Service '$svc' found"
    else
        echo "✗ Service '$svc' MISSING"
        exit 1
    fi
done

# Check volume exists
if docker compose config --volumes | grep -q "bot_data"; then
    echo "✓ Volume 'bot_data' found"
else
    echo "✗ Volume 'bot_data' MISSING"
    exit 1
fi

# Check ports
if docker compose config 2>/dev/null | grep -q 'published: "80"'; then
    echo "✓ Frontend port 80:80 found"
else
    echo "✗ Frontend port 80:80 MISSING"
    exit 1
fi

if docker compose config 2>/dev/null | grep -q 'published: "8000"'; then
    echo "✓ Backend port 8000:8000 found"
else
    echo "✗ Backend port 8000:8000 MISSING"
    exit 1
fi

echo "All docker-compose validations passed!"