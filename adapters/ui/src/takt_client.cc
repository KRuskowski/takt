/// @file takt_client.cc
// Copyright (c) 2026 Einheit Networks

#include "einheit/adapters/takt/takt_client.h"

#include <httplib.h>

#include <chrono>
#include <format>
#include <string>
#include <utility>

namespace einheit::adapters::takt {
namespace {

auto MakeError(TaktClientError code, std::string msg)
    -> ui::Error<TaktClientError> {
  return ui::Error<TaktClientError>{
      code, std::move(msg)};
}

struct ParsedUrl {
  bool tls = false;
  std::string host;
  int port = 80;
};

auto ParseBase(const std::string &url)
    -> std::expected<ParsedUrl,
                     ui::Error<TaktClientError>> {
  ParsedUrl out;
  std::string rest;
  if (url.starts_with("https://")) {
    out.tls = true;
    out.port = 443;
    rest = url.substr(8);
  } else if (url.starts_with("http://")) {
    out.tls = false;
    out.port = 80;
    rest = url.substr(7);
  } else {
    return std::unexpected(MakeError(
        TaktClientError::BadUrl,
        std::format("missing http(s):// in '{}'",
                    url)));
  }
  auto slash = rest.find('/');
  if (slash != std::string::npos) {
    rest = rest.substr(0, slash);
  }
  auto colon = rest.find(':');
  if (colon != std::string::npos) {
    try {
      out.port = std::stoi(rest.substr(colon + 1));
    } catch (...) {
      return std::unexpected(MakeError(
          TaktClientError::BadUrl,
          std::format("bad port in '{}'", url)));
    }
    out.host = rest.substr(0, colon);
  } else {
    out.host = rest;
  }
  if (out.host.empty()) {
    return std::unexpected(MakeError(
        TaktClientError::BadUrl,
        std::format("missing host in '{}'", url)));
  }
  return out;
}

auto BuildClient(const ParsedUrl &url,
                 std::chrono::milliseconds timeout)
    -> std::unique_ptr<httplib::Client> {
  auto c = std::make_unique<httplib::Client>(
      url.host, url.port);
  c->set_read_timeout(timeout);
  c->set_connection_timeout(timeout);
  c->set_keep_alive(false);
  c->enable_server_certificate_verification(url.tls);
  return c;
}

auto ParseJson(const std::string &body)
    -> std::expected<nlohmann::json,
                     ui::Error<TaktClientError>> {
  if (body.empty()) {
    return nlohmann::json::object();
  }
  try {
    return nlohmann::json::parse(body);
  } catch (const std::exception &e) {
    return std::unexpected(MakeError(
        TaktClientError::Json,
        std::format("not JSON: {}", e.what())));
  }
}

auto DoRequest(
    const ParsedUrl &url,
    std::chrono::milliseconds timeout,
    const std::string &method,
    std::string_view path,
    std::string_view req_body = "",
    std::string_view content_type = "application/json")
    -> std::expected<nlohmann::json,
                     ui::Error<TaktClientError>> {
  if (url.host.empty()) {
    return std::unexpected(MakeError(
        TaktClientError::BadUrl, "no host"));
  }
  auto cli = BuildClient(url, timeout);
  httplib::Result res{nullptr, httplib::Error::Unknown};
  auto p = std::string{path};
  auto b = std::string{req_body};
  auto ct = std::string{content_type};
  if (method == "GET") {
    res = cli->Get(p);
  } else if (method == "POST") {
    res = cli->Post(p, b, ct);
  } else if (method == "PUT") {
    res = cli->Put(p, b, ct);
  } else if (method == "DELETE") {
    res = cli->Delete(p);
  }
  if (!res) {
    return std::unexpected(MakeError(
        TaktClientError::Network,
        std::format("{} {} failed: {}", method, path,
                    httplib::to_string(res.error()))));
  }
  if (res->status < 200 || res->status >= 300) {
    return std::unexpected(MakeError(
        TaktClientError::HttpError,
        std::format("{} {} returned {}: {}", method,
                    path, res->status, res->body)));
  }
  auto parsed = ParseJson(res->body);
  if (!parsed) return parsed;
  // Unwrap takt API envelope: {"status":"ok","data":{...}}
  // Then if data has a single key, return its value directly.
  if (parsed->contains("data")) {
    auto &data = (*parsed)["data"];
    if (data.is_object() && data.size() == 1) {
      return data.begin().value();
    }
    return data;
  }
  return parsed;
}

}  // namespace

struct TaktClient::Impl {
  TaktClientConfig cfg;
  ParsedUrl url;
};

TaktClient::TaktClient(TaktClientConfig cfg)
    : impl_(std::make_unique<Impl>()) {
  impl_->cfg = std::move(cfg);
  if (auto p = ParseBase(impl_->cfg.base_url); p) {
    impl_->url = *p;
  }
}

TaktClient::~TaktClient() = default;
TaktClient::TaktClient(TaktClient &&) noexcept = default;
auto TaktClient::operator=(TaktClient &&) noexcept
    -> TaktClient & = default;

auto TaktClient::Get(std::string_view path) const
    -> std::expected<nlohmann::json,
                     ui::Error<TaktClientError>> {
  return DoRequest(impl_->url, impl_->cfg.timeout,
                   "GET", path);
}

auto TaktClient::Post(
    std::string_view path, std::string_view body,
    std::string_view content_type) const
    -> std::expected<nlohmann::json,
                     ui::Error<TaktClientError>> {
  return DoRequest(impl_->url, impl_->cfg.timeout,
                   "POST", path, body, content_type);
}

auto TaktClient::Put(std::string_view path,
                     std::string_view body) const
    -> std::expected<nlohmann::json,
                     ui::Error<TaktClientError>> {
  return DoRequest(impl_->url, impl_->cfg.timeout,
                   "PUT", path, body);
}

auto TaktClient::Delete(std::string_view path) const
    -> std::expected<nlohmann::json,
                     ui::Error<TaktClientError>> {
  return DoRequest(impl_->url, impl_->cfg.timeout,
                   "DELETE", path);
}

}  // namespace einheit::adapters::takt
