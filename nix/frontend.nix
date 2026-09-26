{ pkgs }:
pkgs.buildNpmPackage {
  pname = "qcl-negf-portal-static";
  version = "0.2.0";
  src = pkgs.lib.cleanSourceWith {
    src = ../frontend;
    filter = path: type:
      !(builtins.elem (baseNameOf path) [ "node_modules" "dist" ])
      && pkgs.lib.cleanSourceFilter path type;
  };
  npmDepsHash = (builtins.fromJSON (builtins.readFile ./generated-npm-hash.json)).npmDepsHash;
  nodejs = pkgs.nodejs_24;
  postPatch = ''
    substituteInPlace vite.config.ts \
      --replace-fail "../src/qcl_negf_api/static" "dist"
  '';
  installPhase = ''
    runHook preInstall
    mkdir -p $out
    cp -r dist/. $out/
    runHook postInstall
  '';
  meta = {
    description = "Static browser application for QCL-NEGF scientific workflows";
    homepage = "https://github.com/AfonenkoA/qcl-negf-portal";
    license = pkgs.lib.licenses.mit;
  };
}
