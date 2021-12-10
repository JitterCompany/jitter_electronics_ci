FROM setsoft/kicad_auto:latest

# Create user 'build'
RUN apt-get update && apt-get install -y git
RUN useradd build --create-home --uid 1234

# Setup home directory with config files
RUN mkdir -p /home/build/.config/kicad
WORKDIR /home/build/.config/kicad
COPY assets/fp-lib-table .
COPY assets/sym-lib-table .
COPY assets/kicad_common .

# Note: unfortunately, KiBot does not read this config file (yet?)
# So we have to pre-process the pcb file oursleves (search & replace)
RUN mkdir -p 3d
COPY assets/3Dresolver.cfg ./3d/

# Setup docker entrypoint script
RUN mkdir -p /jitter
WORKDIR /jitter
COPY fix_uid_gid.sh .
COPY entrypoint.sh .
COPY run_ci.sh .
COPY preprocess_board.sh .
COPY pcbnew_do.py .

# Add jitter-specific libraries
# NOTE: config files in ~/.config point here
RUN git clone https://github.com/JitterCompany/KicadComponents.git
RUN chown --recursive build:build /jitter/KicadComponents

RUN mkdir -p /usr/share/kicad/modules/
WORKDIR /usr/share/kicad/modules/
RUN git clone https://gitlab.com/kicad/libraries/kicad-packages3D.git packages3d


RUN mkdir -p /build
WORKDIR /build


ENTRYPOINT ["/jitter/entrypoint.sh"]
