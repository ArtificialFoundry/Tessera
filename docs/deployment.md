# Deployment

```bash
docker build -t tessera .
docker run -p 8780:8780 \
  -e TESSERA_PRIMARY_URL=https://192.0.2.1:53443 \
  -e TESSERA_STANDBY_URL=https://192.0.2.2:53443 \
  -v /etc/tessera:/etc/tessera:ro \
  tessera
```
