CXX ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra
BIN := n2proxy_master
HDRS := src/ngap.h src/lb_core.h

# VM'de: make   (libsctp-dev gerekir)
$(BIN): src/n2proxy_master.cpp $(HDRS)
	$(CXX) $(CXXFLAGS) -o $@ $< -lsctp

# Yerel testler (SCTP gerekmez): birim testleri + TCP-taşımalı mantık testi (pip install pycrate)
test: tests/core_test tests/n2proxy_tcp
	tests/core_test
	python3 -I tests/proxy_logic_test.py tests/n2proxy_tcp

tests/core_test: tests/core_test.cpp $(HDRS) tests/vectors.h
	$(CXX) -std=c++17 -O1 -Wall -Wextra -fsanitize=address,undefined -o $@ $<

tests/n2proxy_tcp: src/n2proxy_master.cpp $(HDRS)
	$(CXX) -std=c++17 -O1 -Wall -Wextra -DN2P_TCP_TEST -fsanitize=address,undefined -o $@ $<

tests/vectors.h: tests/gen_vectors.py
	cd tests && python3 -I gen_vectors.py >/dev/null

# libsctp-dev olmayan makinede gerçek SCTP kod yolunun sözdizimi kontrolü
syntax-sctp:
	$(CXX) -std=c++17 -Wall -Wextra -fsyntax-only -Itests/shim src/n2proxy_master.cpp

clean:
	rm -f $(BIN) tests/core_test tests/n2proxy_tcp

.PHONY: test syntax-sctp clean
