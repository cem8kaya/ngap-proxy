// Yalnızca YEREL SÖZDİZİMİ KONTROLÜ için: libsctp-dev yokken (-fsyntax-only) derlemeyi doğrular. Üretimde gerçek başlık kullanılır.
#pragma once
#include <sys/socket.h>
#include <linux/sctp.h>
extern "C" {
int sctp_sendmsg(int, const void*, size_t, struct sockaddr*, socklen_t, uint32_t, uint32_t, uint16_t, uint32_t, uint32_t);
int sctp_recvmsg(int, void*, size_t, struct sockaddr*, socklen_t*, struct sctp_sndrcvinfo*, int*);
}
