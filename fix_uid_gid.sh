#!/usr/bin/env bash

if [ $# -lt 2 ]; then
    echo "invalid arguments: please specify UID and GID:"
    echo "try '$0 <UID> <GID>'"

    exit 1
fi

echo "changing 'build' to UID $1, GID $2"

usermod -u $1 build
groupmod -g $2 build

chown -R build /home/build
chgrp -R build /home/build

chown -R build /jitter/KicadComponents
chgrp -R build /jitter/KicadComponents

