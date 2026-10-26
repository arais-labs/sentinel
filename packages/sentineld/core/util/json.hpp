// Minimal JSON for the control API, the catalog and install recipes.
#pragma once
#include <cstdint>
#include <map>
#include <stdexcept>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

namespace sentineld::json {

// Thrown when a value has the wrong type: a malformed request, never an internal failure.
struct TypeError : std::runtime_error {
  using std::runtime_error::runtime_error;
};

struct Value;
using Object = std::map<std::string, Value, std::less<>>;
using Array = std::vector<Value>;

struct Value {
  std::variant<std::nullptr_t, bool, double, std::string, Array, Object> v;

  Value() : v(nullptr) {}
  Value(std::nullptr_t) : v(nullptr) {}
  Value(bool b) : v(b) {}
  Value(int n) : v(double(n)) {}
  Value(int64_t n) : v(double(n)) {}
  Value(uint64_t n) : v(double(n)) {}
  Value(double n) : v(n) {}
  Value(const char* s) : v(std::string(s)) {}
  Value(std::string s) : v(std::move(s)) {}
  Value(Array a) : v(std::move(a)) {}
  Value(Object o) : v(std::move(o)) {}

  const Value& operator[](std::string_view key) const {
    static const Value null;
    if (auto* o = std::get_if<Object>(&v)) {
      auto it = o->find(key);
      if (it != o->end()) return it->second;
    }
    return null;
  }
  bool isNull() const { return std::holds_alternative<std::nullptr_t>(v); }
  const std::string& str() const { return as<std::string>("string"); }
  const Array& arr() const { return as<Array>("array"); }
  double num() const { return as<double>("number"); }
  bool boolean() const { return as<bool>("boolean"); }
  const Object* obj() const { return std::get_if<Object>(&v); }

 private:
  template <class T> const T& as(const char* what) const {
    if (auto* p = std::get_if<T>(&v)) return *p;
    throw TypeError(std::string("expected ") + what);
  }
};

Value parse(std::string_view text);
std::string dump(const Value& v);

}  // namespace sentineld::json
