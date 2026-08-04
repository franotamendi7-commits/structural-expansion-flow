#!/bin/bash
set -e
echo "=== Starting Trading Bot + Dashboard via supervisord ==="
exec supervisord -c supervisord.conf
