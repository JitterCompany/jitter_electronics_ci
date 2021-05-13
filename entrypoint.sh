#!/usr/bin/env bash

if ! [ -z "$HOST_UID" ] && ! [ -z "$HOST_GID" ]; then
    echo "Setting UID to $HOST_UID, GID to $HOST_GID.."
    /jitter/fix_uid_gid.sh "$HOST_UID" "$HOST_GID"
else
    echo "HOST_UID=$HOST_UID, HOST_GID=$HOST_GID"
    echo "Note: if you experience permission issues when mounting /build on the host,"
    echo "consider setting HOST_UID, HOST_GID environment variables"
fi

# Drop privileges to user 'build'
echo "Dropping privileges: switching to user 'build'"
su - build -c "cd $(pwd); $@"

