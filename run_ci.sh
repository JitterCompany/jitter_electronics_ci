#!/usr/bin/env bash


# Parse input args
if [ -z "$1" ] || [ -z "$2" ]; then
    echo "Usage: `basename $0` <project_name>, <out_dir>"
    echo ""
    echo "The CI script expects the following files"
    echo "to exist: "
    echo "- <project_name>/<project_name>.sch"
    echo "- <project_name>/<project_name>.kicad_pcb"
    echo "- <project_name>/<project_name>.kibot.yaml"
    exit 1
fi
PROJECT_NAME="$1"
OUT_DIR="$2"

# consume arguments passed to the current bash script.
# (all remaining args will be passed on to kibot using "$@" )
shift
shift


# Copy project dir to tmp folder (they will be edited by preprocess script)
TMP_DIR="/tmp/${PROJECT_NAME}"
cp --recursive "${PROJECT_NAME}" "${TMP_DIR}"


CONFIG_FILE="${TMP_DIR}/${PROJECT_NAME}.kibot.yaml"
BOARD_FILE="${TMP_DIR}/${PROJECT_NAME}.kicad_pcb"
SCHEMATIC_FILE="/tmp/${PROJECT_NAME}/${PROJECT_NAME}.sch"


echo "Preprocessing board file..."
/jitter/preprocess_board.sh "${BOARD_FILE}"
echo "Running KiBot..."
kibot \
    --plot-config "${CONFIG_FILE}" \
    --schematic "${SCHEMATIC_FILE}" \
    --board-file "${BOARD_FILE}" \
    --out-dir "${OUT_DIR}" \
    "$@"

# TODO 3D footprints from kicad libraries are missing. either package them with docker, or re-use the downloaded files from 3D step builder?
echo "Exporting 3D render..."
RENDER_FILE="${PROJECT_NAME}-render.png"
/jitter/pcbnew_do.py --rec_width 2560 --rec_height 1440 render_3d --output_name "${RENDER_FILE}" "${BOARD_FILE}" "${OUT_DIR}"

echo "Cleaning up..."
rm -rf "${TMP_DIR}"

