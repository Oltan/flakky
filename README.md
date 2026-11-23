# flakky / triaj

Test sonuçlarından flaky testleri tespit eden ve başarısızlıkları sınıflandıran
(`test_kodu` / `urun` / `ortam` / `flaky` / `bilinmiyor`) Python mikroservisi.
Kural kararı anında verilir; LLM değerlendirmesi OpenAI uyumlu gateway üzerinden kuyrukla yapılır.
Allure ve JUnit çıktıları CI istemcisi (`triaj`) ile yüklenir.

- Tasarım: [docs/tasarim.md](docs/tasarim.md)
- Uygulama planı ve kararlar: [PLAN.md](PLAN.md)

## Hızlı başlangıç (Docker)

```bash
docker compose up -d --build
```

```bash
curl -H "Authorization: Bearer yerel-token" localhost:8000/readyz
```

```bash
python tools/senaryo.py --url http://localhost:8000 --token yerel-token --llm http://localhost:8001
```

`docker-compose.yml` postgres + api + worker + sahte LLM (`tools/sahte_llm.py`) kurar.

## Yerel geliştirme

```bash
pip install -e ".[dev]"
```

```bash
pytest -q
```

Postgres'e karşı da koşmak için (CI'da zorunlu):

```bash
TRIAJ_TEST_PG_URL=postgresql://kullanici@localhost/triaj_test pytest -q
```

Servisi elle başlatma:

```bash
alembic upgrade head
```

```bash
uvicorn --factory triaj.api:app_factory --port 8000
```

```bash
python -m triaj.worker
```

## CI'da kullanım (GitLab)

```yaml
test:
  script:
    - mvn test
  after_script:
    - pip install "triaj @ git+https://<sunucu>/<grup>/flakky.git"
    - triaj upload --format allure --dir target/allure-results --context --repo . --finish
    - triaj wait --timeout 600   # isteğe bağlı: LLM kararlarını bekleyip özet basar
  variables:
    TRIAJ_URL: https://triaj.example.com
    # TRIAJ_TOKEN: GitLab CI/CD değişkeni olarak (masked) tanımlayın
```

Proje/branch/commit/koşum `CI_PROJECT_PATH`, `CI_COMMIT_REF_NAME`, `CI_COMMIT_SHA`, `CI_JOB_ID`'den okunur.
Sunucuya ulaşılamazsa uyarı basılır ve çıkış kodu 0 olur (`--strict` ile 1).

## API (özet)

| Uç | İş |
|---|---|
| `POST /v1/runs` | Koşum + sonuçlar (external_id ile idempotent) |
| `POST /v1/runs/{id}/finish` | Kural kararları + LLM kuyruğu |
| `GET /v1/runs/{id}/triage` | Kalan sonuçlar: flaky, kanıt, karar, LLM durumu, next_action |
| `GET /v1/projects/{p}/flaky?branch=` | Flaky listesi + kanıt |
| `GET /v1/projects/{p}/tests/history?test_key=&branch=&limit=` | Test geçmişi |
| `GET/POST /v1/results/{id}/decisions` | Karar geçmişi / admin kararı |
| `GET /v1/projects/{p}/llm-accuracy` | Admin kararlarına göre LLM ve kural isabeti |
| `GET/PUT /v1/projects/{p}/settings` | Kalıplar, ipuçları, eşikler, llm_enabled |
| `GET /healthz`, `/readyz` | Sağlık |

Proje adı `/` içerebilir (`grup/alt/proje`). Ortam değişkenleri: [src/triaj/config.py](src/triaj/config.py).

## Örnek veri

`tests/data/` gerçek araç çıktılarıdır; `tools/ornek/uret.sh` ile yeniden üretilir
(pytest + allure-pytest, JUnit 5 + surefire rerun, Cucumber 7 + allure-cucumber7-jvm).
