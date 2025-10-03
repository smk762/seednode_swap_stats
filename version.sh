#!/bin/bash
userpass=$(cat MM2.json | jq -r '.rpc_password')
port=$(cat MM2.json | jq -r '.rpcport')
curl --url "http://127.0.0.1:$port" --data "{\"method\":\"version\",\"userpass\":\"$userpass\"}"
echo ""
