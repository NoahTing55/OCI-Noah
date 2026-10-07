FROM nginx:alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY index.html /usr/share/nginx/html/index.html
COPY styles.css /usr/share/nginx/html/styles.css
COPY ui21-preview.css /usr/share/nginx/html/ui21-preview.css
COPY app.js /usr/share/nginx/html/app.js
COPY rc.js /usr/share/nginx/html/rc.js
COPY ui2.js /usr/share/nginx/html/ui2.js
COPY vnc.html /usr/share/nginx/html/vnc.html

RUN chmod 644 \
      /etc/nginx/conf.d/default.conf \
      /usr/share/nginx/html/index.html \
      /usr/share/nginx/html/styles.css \
      /usr/share/nginx/html/ui21-preview.css \
      /usr/share/nginx/html/app.js \
      /usr/share/nginx/html/rc.js \
      /usr/share/nginx/html/ui2.js \
      /usr/share/nginx/html/vnc.html \
    && chmod 755 /usr/share/nginx/html

EXPOSE 9859

# OCI-N&T favicon
COPY favicon.svg /usr/share/nginx/html/favicon.svg
COPY favicon.ico /usr/share/nginx/html/favicon.ico
COPY apple-touch-icon.png /usr/share/nginx/html/apple-touch-icon.png
