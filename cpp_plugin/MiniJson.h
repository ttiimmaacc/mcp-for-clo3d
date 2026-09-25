// Minimal JSON value, parser and serializer (enough for the MCP request/response protocol).
#pragma once

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace mj
{
struct Value
{
	enum Type { Null, Bool, Number, String, Array, Object };

	Type type = Null;
	bool b = false;
	double n = 0.0;
	std::string s;
	std::vector<Value> a;
	std::vector<std::pair<std::string, Value>> o; // keeps insertion order

	Value() {}
	Value(bool v) : type(Bool), b(v) {}
	Value(int v) : type(Number), n(v) {}
	Value(unsigned int v) : type(Number), n(v) {}
	Value(double v) : type(Number), n(v) {}
	Value(float v) : type(Number), n(v) {}
	Value(const char* v) : type(String), s(v ? v : "") {}
	Value(const std::string& v) : type(String), s(v) {}

	static Value array() { Value v; v.type = Array; return v; }
	static Value object() { Value v; v.type = Object; return v; }

	template <class T>
	static Value from(const std::vector<T>& list)
	{
		Value v = array();
		for (const auto& item : list)
			v.a.push_back(Value(item));
		return v;
	}

	static Value from(const std::map<std::string, std::string>& m)
	{
		Value v = object();
		for (const auto& kv : m)
			v.set(kv.first, kv.second);
		return v;
	}

	static Value from(const std::vector<std::map<std::string, std::string>>& list)
	{
		Value v = array();
		for (const auto& m : list)
			v.a.push_back(from(m));
		return v;
	}

	Value& set(const std::string& key, const Value& v)
	{
		type = Object;
		for (auto& kv : o)
			if (kv.first == key) { kv.second = v; return *this; }
		o.emplace_back(key, v);
		return *this;
	}

	const Value* find(const std::string& key) const
	{
		if (type != Object) return nullptr;
		for (const auto& kv : o)
			if (kv.first == key) return &kv.second;
		return nullptr;
	}

	bool has(const std::string& key) const
	{
		const Value* v = find(key);
		return v && v->type != Null;
	}

	double num(const std::string& key, double def) const
	{
		const Value* v = find(key);
		if (!v || v->type == Null) return def;
		if (v->type == Number) return v->n;
		if (v->type == Bool) return v->b ? 1.0 : 0.0;
		throw std::runtime_error("parameter '" + key + "' must be a number");
	}

	double num(const std::string& key) const
	{
		if (!has(key)) throw std::runtime_error("missing parameter '" + key + "'");
		return num(key, 0.0);
	}

	bool boolean(const std::string& key, bool def) const
	{
		const Value* v = find(key);
		if (!v || v->type == Null) return def;
		if (v->type == Bool) return v->b;
		if (v->type == Number) return v->n != 0.0;
		throw std::runtime_error("parameter '" + key + "' must be a boolean");
	}

	std::string str(const std::string& key) const
	{
		const Value* v = find(key);
		if (!v || v->type != String) throw std::runtime_error("missing string parameter '" + key + "'");
		return v->s;
	}

	std::string str(const std::string& key, const std::string& def) const
	{
		const Value* v = find(key);
		return (v && v->type == String) ? v->s : def;
	}
};

inline void dumpString(const std::string& s, std::string& out)
{
	out += '"';
	for (unsigned char c : s)
	{
		switch (c)
		{
		case '"': out += "\\\""; break;
		case '\\': out += "\\\\"; break;
		case '\n': out += "\\n"; break;
		case '\r': out += "\\r"; break;
		case '\t': out += "\\t"; break;
		default:
			if (c < 0x20)
			{
				char buf[8];
				snprintf(buf, sizeof(buf), "\\u%04x", c);
				out += buf;
			}
			else
				out += (char)c;
		}
	}
	out += '"';
}

inline void dump(const Value& v, std::string& out)
{
	switch (v.type)
	{
	case Value::Null: out += "null"; break;
	case Value::Bool: out += v.b ? "true" : "false"; break;
	case Value::Number:
	{
		char buf[64];
		if (v.n == (double)(long long)v.n && v.n > -1e15 && v.n < 1e15)
			snprintf(buf, sizeof(buf), "%lld", (long long)v.n);
		else
			snprintf(buf, sizeof(buf), "%.17g", v.n);
		out += buf;
		break;
	}
	case Value::String: dumpString(v.s, out); break;
	case Value::Array:
		out += '[';
		for (size_t i = 0; i < v.a.size(); ++i)
		{
			if (i) out += ", ";
			dump(v.a[i], out);
		}
		out += ']';
		break;
	case Value::Object:
		out += '{';
		for (size_t i = 0; i < v.o.size(); ++i)
		{
			if (i) out += ", ";
			dumpString(v.o[i].first, out);
			out += ": ";
			dump(v.o[i].second, out);
		}
		out += '}';
		break;
	}
}

inline std::string dump(const Value& v)
{
	std::string out;
	dump(v, out);
	return out;
}

class Parser
{
public:
	explicit Parser(const std::string& text) : t(text) {}

	Value parse()
	{
		Value v = value();
		ws();
		if (i != t.size()) fail("trailing characters");
		return v;
	}

private:
	const std::string& t;
	size_t i = 0;

	[[noreturn]] void fail(const char* what)
	{
		throw std::runtime_error(std::string("Invalid JSON: ") + what + " at offset " + std::to_string(i));
	}

	void ws()
	{
		while (i < t.size() && (t[i] == ' ' || t[i] == '\n' || t[i] == '\r' || t[i] == '\t')) ++i;
	}

	bool lit(const char* word)
	{
		size_t len = strlen(word);
		if (t.compare(i, len, word) == 0) { i += len; return true; }
		return false;
	}

	Value value()
	{
		ws();
		if (i >= t.size()) fail("unexpected end");
		char c = t[i];
		if (c == '{') return object();
		if (c == '[') return array();
		if (c == '"') return Value(string());
		if (lit("true")) return Value(true);
		if (lit("false")) return Value(false);
		if (lit("null")) return Value();
		return number();
	}

	Value object()
	{
		Value v = Value::object();
		++i;
		ws();
		if (i < t.size() && t[i] == '}') { ++i; return v; }
		while (true)
		{
			ws();
			if (i >= t.size() || t[i] != '"') fail("expected key");
			std::string key = string();
			ws();
			if (i >= t.size() || t[i] != ':') fail("expected ':'");
			++i;
			v.o.emplace_back(key, value());
			ws();
			if (i < t.size() && t[i] == ',') { ++i; continue; }
			if (i < t.size() && t[i] == '}') { ++i; return v; }
			fail("expected ',' or '}'");
		}
	}

	Value array()
	{
		Value v = Value::array();
		++i;
		ws();
		if (i < t.size() && t[i] == ']') { ++i; return v; }
		while (true)
		{
			v.a.push_back(value());
			ws();
			if (i < t.size() && t[i] == ',') { ++i; continue; }
			if (i < t.size() && t[i] == ']') { ++i; return v; }
			fail("expected ',' or ']'");
		}
	}

	static void utf8(unsigned cp, std::string& out)
	{
		if (cp < 0x80) out += (char)cp;
		else if (cp < 0x800) { out += (char)(0xC0 | (cp >> 6)); out += (char)(0x80 | (cp & 0x3F)); }
		else if (cp < 0x10000) { out += (char)(0xE0 | (cp >> 12)); out += (char)(0x80 | ((cp >> 6) & 0x3F)); out += (char)(0x80 | (cp & 0x3F)); }
		else { out += (char)(0xF0 | (cp >> 18)); out += (char)(0x80 | ((cp >> 12) & 0x3F)); out += (char)(0x80 | ((cp >> 6) & 0x3F)); out += (char)(0x80 | (cp & 0x3F)); }
	}

	unsigned hex4()
	{
		if (i + 4 > t.size()) fail("bad \\u escape");
		unsigned cp = (unsigned)strtoul(t.substr(i, 4).c_str(), nullptr, 16);
		i += 4;
		return cp;
	}

	std::string string()
	{
		std::string out;
		++i; // opening quote
		while (i < t.size())
		{
			char c = t[i++];
			if (c == '"') return out;
			if (c != '\\') { out += c; continue; }
			if (i >= t.size()) break;
			char e = t[i++];
			switch (e)
			{
			case '"': out += '"'; break;
			case '\\': out += '\\'; break;
			case '/': out += '/'; break;
			case 'b': out += '\b'; break;
			case 'f': out += '\f'; break;
			case 'n': out += '\n'; break;
			case 'r': out += '\r'; break;
			case 't': out += '\t'; break;
			case 'u':
			{
				unsigned cp = hex4();
				if (cp >= 0xD800 && cp <= 0xDBFF && i + 6 <= t.size() && t[i] == '\\' && t[i + 1] == 'u')
				{
					i += 2;
					unsigned lo = hex4();
					cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
				}
				utf8(cp, out);
				break;
			}
			default: fail("bad escape");
			}
		}
		fail("unterminated string");
	}

	Value number()
	{
		const char* start = t.c_str() + i;
		char* end = nullptr;
		double d = strtod(start, &end);
		if (end == start) fail("unexpected character");
		i += (size_t)(end - start);
		return Value(d);
	}
};

inline Value parse(const std::string& text)
{
	return Parser(text).parse();
}
} // namespace mj
