FROM nowsci/samba-domain

COPY docker/samba-ad/provision-ad.sh /provision-ad.sh

# run the AD provisioner alongside the upstream init - which provisions the
# domain on first start, then runs samba under supervisord
CMD ["/bin/bash", "-c", "(/provision-ad.sh 2>&1 | sed 's/^/[provision-ad] /' &) && exec /init.sh"]
