{
  description = "Declarative, container-based agent evals with attestable bundles";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { nixpkgs, ... }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
      pythonFor = pkgs: pkgs.python3.withPackages (ps: [ ps.pyyaml ps.jsonschema ]);
    in {
      devShells = forAllSystems (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          default = pkgs.mkShell {
            packages = [
              (pythonFor pkgs)
              pkgs.actionlint
              pkgs.check-jsonschema
              pkgs.git
              pkgs.jq
            ];
            shellHook = ''
              export PYTHONPATH="$PWD/src''${PYTHONPATH:+:$PYTHONPATH}"
              alias evals="python3 -m evals.cli"
            '';
          };
        });

      packages = forAllSystems (system:
        let pkgs = import nixpkgs { inherit system; };
        in nixpkgs.lib.optionalAttrs pkgs.stdenv.isLinux {
          # The agent container: pi + git + a POSIX userland. Load with
          #   nix build .#agent-image && podman load < result
          agent-image = pkgs.dockerTools.buildLayeredImage {
            name = "localhost/evals-agent";
            tag = "latest";
            contents = [
              pkgs.pi-coding-agent
              pkgs.gitMinimal
              pkgs.coreutils
              pkgs.gnugrep
              pkgs.gnused
              pkgs.findutils
              pkgs.bashInteractive
              pkgs.python3
              pkgs.dockerTools.binSh
              pkgs.dockerTools.usrBinEnv
              pkgs.dockerTools.caCertificates
            ];
            config = {
              Env = [
                "PATH=/bin:/usr/bin"
                "HOME=/workspace"
              ];
              WorkingDir = "/workspace";
              User = "1000:1000";
            };
          };
        });
    };
}
