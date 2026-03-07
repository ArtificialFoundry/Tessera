FROM alpine:3.21

RUN apk add --no-cache bash curl openssl nmap nmap-scripts coreutils grep

COPY voter/tessera-voter.sh /usr/local/bin/tessera-voter.sh
RUN chmod +x /usr/local/bin/tessera-voter.sh

# In container context, nmap runs as root directly — patch out sudo
RUN sed -i 's/sudo nmap/nmap/g' /usr/local/bin/tessera-voter.sh

COPY voter/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV VOTER_INTERVAL=30

ENTRYPOINT ["/entrypoint.sh"]
