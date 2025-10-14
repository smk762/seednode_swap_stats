#!/bin/bash
userpass=$(cat MM2.json | jq -r '.rpc_password')
port=$(cat MM2.json | jq -r '.rpcport')
rm coins || true
wget https://raw.githubusercontent.com/KomodoPlatform/coins/refs/heads/master/coins
rm ~/kdf/kdf.log || true
stdbuf -oL kdf > ~/kdf/kdf.log &
sleep 3
curl --url "http://127.0.0.1:$port" --data "{\"method\":\"version\",\"userpass\":\"$userpass\"}"
tail -f ~/kdf/kdf.log