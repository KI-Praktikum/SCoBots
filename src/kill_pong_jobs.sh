#! /bin/sh

for pid in $(ps -ef | grep "SeSz_pong" | awk '{print $2}'); do kill -9 $pid; done