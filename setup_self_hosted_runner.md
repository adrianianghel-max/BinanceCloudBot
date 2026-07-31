# Setup Self-Hosted Runner

Binance Global blocheaza IP-urile cloud GitHub Actions (451). Pentru a scana toate 274 simbolurile USDC, runner-ul trebuie sa ruleze pe PC-ul tau (IP Romania).

## Pasul 1 - Adauga runner-ul

1. Mergi pe: https://github.com/adrianianghel-max/BinanceCloudBot/settings/actions/runners/new
2. OS: Windows, Architecture: x64
3. Ruleaza comenzile afisate in cmd pe PC (download + config + run)
4. Adauga label: **self-hosted**

## Pasul 2 - Workflow

Workflow-ul `.github/workflows/scan.yml` ruleaza deja cu `runs-on: self-hosted`. Cand runner-ul e online, scanul complet ruleaza la fiecare 15 minute.

## Verificare

- Runner Idle in Settings → Actions → Runners
- Scaneaza toate 274+ simboluri USDC
- Alerte Telegram trimise normal