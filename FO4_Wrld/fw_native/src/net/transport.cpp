#include "transport.h"

#include <string>

#include "../log.h"
#include "udp_socket.h"

namespace fw::net {

namespace {

// The one and only transport today: plain UDP to cfg.server_host:server_port,
// exactly as the client always did. When FoM is tunnelling a session over
// Steam, the "server" on a joining machine is FoM's local bridge endpoint -
// which is why this stays 127.0.0.1 and the player never types an IP.
class UdpTransport final : public ITransport {
public:
    UdpTransport(std::string host, std::uint16_t port)
        : host_(std::move(host)), port_(port) {}

    bool open() override { return sock_.open(host_, port_); }
    void close() override { sock_.close(); }

    bool send(const void* data, std::size_t len) override {
        return sock_.send(data, len);
    }

    int recv(void* buffer, std::size_t buffer_len, int timeout_ms) override {
        return sock_.recv(buffer, buffer_len, timeout_ms);
    }

    bool is_open()    const noexcept override { return sock_.is_open(); }
    int  last_error() const noexcept override { return sock_.last_error(); }
    const char* name() const noexcept override { return "udp"; }

private:
    std::string   host_;
    std::uint16_t port_;
    UdpSocket     sock_;
};

}  // namespace

std::unique_ptr<ITransport> make_transport(const config::Settings& cfg) {
    return std::make_unique<UdpTransport>(cfg.server_host, cfg.server_port);
}

}  // namespace fw::net
