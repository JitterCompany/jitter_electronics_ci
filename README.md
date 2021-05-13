# jitter_electronics_ci

CI tools to automatically build / export / convert electronics design files


## Build the docker image

```
./build_docker_img.sh
```

## Usage

```
PROJECT_FOLDER="my-project"
OUT_DIR="my-output-folder"

# User 'build' inside docker image must match USER_ID outside docker,
# otherwise file permissions will be conflicting
USER_ID=$(id -u)
GROUP_ID=$(id -g)

docker run \
    --rm -it \
    -v $(pwd):/build \
    --env HOST_UID="$USER_ID" --env HOST_GID="$GROUP_ID" \
    jittercompany/jitter_electronics_ci:0.1 \
    "/jitter/run_ci.sh \"${FOLDER}\" \"${OUT_DIR}\" -v"
```

This script snippet assumes a project folder containing these files:
- <project name>.kibot.yaml (See kibot project for the options)
- <project name>.kicad_pcb
- <project name>.sch
