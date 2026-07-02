#!/bin/bash

cd web-studio && npm run build -- --base="/studio/" && rm -rf ../openviking/web_studio/dist && cp -r dist ../openviking/web_studio/dist
