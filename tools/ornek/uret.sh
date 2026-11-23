#!/usr/bin/env bash
# Örnek projeleri gerçek araçlarla koşturup çıktılarını tests/data/ altına kopyalar.
# Gerekenler: python (pytest, allure-pytest, pytest-rerunfailures), JDK 17+, Maven 3.9+.
set -euo pipefail
KOK="$(cd "$(dirname "$0")/../.." && pwd)"
ORNEK="$KOK/tools/ornek"
VERI="$KOK/tests/data"

python3 -m pip show allure-pytest pytest-rerunfailures >/dev/null 2>&1 \
  || python3 -m pip install -q pytest allure-pytest pytest-rerunfailures

echo "== pytest"
rm -rf "$VERI/pytest" && mkdir -p "$VERI/pytest"
(cd "$ORNEK/pytest" && python3 -m pytest -q -p no:cacheprovider --reruns 2 \
   --alluredir "$VERI/pytest/allure-results" --junitxml "$VERI/pytest/junit.xml") || true

echo "== maven-junit"
rm -rf "$VERI/maven-junit" && mkdir -p "$VERI/maven-junit"
(cd "$ORNEK/maven-junit" && mvn -q -B clean test) || true
cp "$ORNEK"/maven-junit/target/surefire-reports/TEST-*.xml "$VERI/maven-junit/"

echo "== maven-cucumber"
rm -rf "$VERI/maven-cucumber" && mkdir -p "$VERI/maven-cucumber"
(cd "$ORNEK/maven-cucumber" && mvn -q -B clean test) || true
cp -r "$ORNEK/maven-cucumber/target/allure-results" "$VERI/maven-cucumber/"
cp "$ORNEK"/maven-cucumber/target/surefire-reports/TEST-*.xml "$VERI/maven-cucumber/"

echo "Tamam: $VERI"
