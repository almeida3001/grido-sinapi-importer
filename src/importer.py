"""
Grido SINAPI Importer
======================
Importa a base SINAPI mensal pro Supabase do Grido usando o toolkit open-source
autoSINAPI (LAMP-LUCAS, GPLv3) como dependência.

O autoSINAPI cuida de:
  - download oficial direto da Caixa
  - descompactação
  - parser dos Excel (CSD/CCD/CSE)
  - persistência no Postgres

Este script é só um wrapper que mapeia DATABASE_URL do Supabase pra os campos
esperados pelo autosinapi.run_etl.

Uso:
    python src/importer.py [--mes 2026-04] [--tipo REFERENCIA]

Variáveis de ambiente (secrets do GitHub Actions):
    DATABASE_URL — postgresql://postgres:PASS@db.xxx.supabase.co:5432/postgres

Licença: GPLv3 (herda de autosinapi).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime
from urllib.parse import urlparse

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("sinapi-importer")


def parse_database_url(url: str) -> dict:
    p = urlparse(url)
    if p.scheme not in ("postgres", "postgresql"):
        raise ValueError(f"Esquema inválido em DATABASE_URL: {p.scheme}")
    if not p.password:
        raise ValueError("DATABASE_URL sem senha")
    return {
        "host": p.hostname or "localhost",
        "port": p.port or 5432,
        "database": (p.path or "/postgres").lstrip("/"),
        "user": p.username or "postgres",
        "password": p.password,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mes", default=None, help="YYYY-MM (default: mês passado)")
    p.add_argument("--tipo", default="REFERENCIA", choices=["REFERENCIA", "MANUTENCOES"])
    p.add_argument("--policy", default="substituir", choices=["substituir", "manter"])
    p.add_argument("--zip-local", default=None, help="Caminho pra ZIP SINAPI já baixado")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args()

    if args.mes:
        ref = datetime.strptime(args.mes + "-01", "%Y-%m-%d").date()
    else:
        today = date.today()
        ref = date(today.year - 1, 12, 1) if today.month == 1 else date(today.year, today.month - 1, 1)

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        log.error("DATABASE_URL não configurada nos secrets")
        sys.exit(2)

    try:
        db_config = parse_database_url(db_url)
    except ValueError as e:
        log.error("DATABASE_URL inválida: %s", e)
        sys.exit(2)

    sinapi_config = {
        "year": ref.year,
        "month": ref.month,
        "type": args.tipo,
        "duplicate_policy": args.policy,
    }

    log.info(
        "Disparando autoSINAPI · ref=%04d-%02d · tipo=%s · destino=%s",
        ref.year, ref.month, args.tipo, db_config["host"],
    )

    if args.zip_local:
        log.info("Modo arquivo local: %s", args.zip_local)
        os.environ["AUTOSINAPI_SKIP_DOWNLOAD"] = "true"
        # autosinapi procura o ZIP em ./downloads/{YYYY}_{MM}/ por padrão
        from pathlib import Path
        import shutil
        target_dir = Path("downloads") / f"{ref.year}_{ref.month:02d}"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_zip = target_dir / Path(args.zip_local).name
        if Path(args.zip_local).resolve() != target_zip.resolve():
            shutil.copy2(args.zip_local, target_zip)
        log.info("ZIP copiado pra %s", target_zip)

    from autosinapi import run_etl

    result = run_etl(
        db_config=db_config,
        sinapi_config=sinapi_config,
        mode="server",
        log_level=args.log_level,
    )

    log.info("Resultado: %s", json.dumps(result, default=str))

    status = (result or {}).get("status", "").upper() if isinstance(result, dict) else ""
    if status in ("FALHA", "FAILED", "ERROR"):
        log.error("Pipeline falhou: %s", result.get("message"))
        sys.exit(3)

    inserted = (result or {}).get("records_inserted", 0)
    log.info("✅ SINAPI %04d-%02d importado: %d registros.", ref.year, ref.month, inserted)


if __name__ == "__main__":
    main()
