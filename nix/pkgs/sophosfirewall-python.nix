# sophosfirewall-python: the API client the sophos.sophos_firewall Ansible collection imports.
# Not in nixpkgs. Pinned to the PyPI sdist; lives in dotsAdhanPython beside ansible-core.
#
# xmltodict is pinned upstream to <0.14 while nixpkgs ships 1.x. The pin is relaxed because the
# library calls only xmltodict.parse and xmltodict.unparse, whose behaviour did not change
# across that boundary. pythonImportsCheck below fails the build if the import ever breaks.
{ python3Packages, fetchPypi }:
python3Packages.buildPythonPackage rec {
  pname = "sophosfirewall-python"; version = "0.1.68";
  pyproject = true;
  src = fetchPypi {
    pname = "sophosfirewall_python"; inherit version;
    sha256 = "8df25f09f23f520e9291146bf2f2f54b86e9cacb65ced9a41443ea06d7f5a7c1";
  };
  build-system = [ python3Packages.poetry-core ];
  dependencies = with python3Packages; [ jinja2 requests xmltodict ];
  pythonRelaxDeps = [ "xmltodict" ];
  pythonImportsCheck = [ "sophosfirewall_python.firewallapi" ];
}
