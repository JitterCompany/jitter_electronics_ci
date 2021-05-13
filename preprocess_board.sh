#!/usr/bin/env bash

BOARD_FILE="$1"
if [ -z "$BOARD_FILE" ]; then
    echo "Expecting </path/to/board.kicad_pcb>"
    echo ""
    echo "No board was specified: nothing to preprocess!"
    exit 1
fi


# Resolve 3D footprint paths
# 
# Kicad normally does this via the ~/.config/kicad/3d/3Dresolver.cfg, 
# but unfortunately KiBot does not support that (yet?)
#

# ':jitter:' resolves to the jitter KicadComponents library
sed -i 's#:jitter:#/jitter/KicadComponents/3D/#g' "${BOARD_FILE}"
