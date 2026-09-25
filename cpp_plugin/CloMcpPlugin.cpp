// CLO MCP listener as a native CLO plug-in.
//
// CLO's embedded Python cannot serve requests without blocking CLO's UI, so this plug-in
// does it natively: a hidden message-only window with a Win32 timer lives on CLO's UI
// thread. CLO's own event loop dispatches the timer, so every request is handled on the
// main thread between UI events and CLO stays responsive.
//
// Protocol is unchanged from plugin/clo3d_mcp_plugin.py: the MCP server writes
// %TEMP%/clo3d_mcp/request.json, the plug-in answers in response.json (temp + rename).
// An empty file named "stop" in that folder, or the Plugins > Plug-in menu action,
// stops the listener.

#include <windows.h>

#include <cstdio>
#include <ctime>
#include <functional>
#include <map>
#include <string>
#include <tuple>
#include <vector>

#include "CLOAPIInterface.h"
#include "LibraryWindowInterface.h"
#include "MiniJson.h"

using mj::Value;

namespace
{
const UINT_PTR TIMER_ID = 1;
const UINT TIMER_MS = 50;
const UINT WM_STOP_LISTENER = WM_APP + 1; // lets another copy of this DLL stop the listener
const wchar_t* WINDOW_CLASS = L"CloMcpListenerWindow";

HWND g_window = nullptr;
bool g_busy = false;
unsigned long long g_ticks = 0;
unsigned long long g_handled = 0;
DWORD g_lastStatus = 0;
std::string g_lastError;

// ---------------------------------------------------------------------------
// Files

std::wstring CommDir()
{
	wchar_t buf[MAX_PATH];
	DWORD len = GetEnvironmentVariableW(L"CLO3D_MCP_DIR", buf, MAX_PATH);
	if (len > 0 && len < MAX_PATH)
		return buf;
	len = GetEnvironmentVariableW(L"TEMP", buf, MAX_PATH); // same as the Python plugin
	if (len == 0 || len >= MAX_PATH)
		len = GetTempPathW(MAX_PATH, buf);
	std::wstring dir(buf, len);
	if (!dir.empty() && dir.back() != L'\\')
		dir += L'\\';
	return dir + L"clo3d_mcp";
}

std::wstring CommFile(const wchar_t* name)
{
	return CommDir() + L"\\" + name;
}

bool Exists(const std::wstring& path)
{
	return GetFileAttributesW(path.c_str()) != INVALID_FILE_ATTRIBUTES;
}

bool ReadAll(const std::wstring& path, std::string& out)
{
	FILE* f = _wfopen(path.c_str(), L"rb");
	if (!f)
		return false;
	char buf[65536];
	size_t n;
	out.clear();
	while ((n = fread(buf, 1, sizeof(buf), f)) > 0)
		out.append(buf, n);
	fclose(f);
	return true;
}

bool WriteAtomic(const std::wstring& path, const std::string& data)
{
	std::wstring tmp = path + L".tmp";
	FILE* f = _wfopen(tmp.c_str(), L"wb");
	if (!f)
		return false;
	fwrite(data.data(), 1, data.size(), f);
	fclose(f);
	return MoveFileExW(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING) != 0;
}

std::string Narrow(const std::wstring& w)
{
	if (w.empty())
		return "";
	int len = WideCharToMultiByte(CP_UTF8, 0, w.c_str(), (int)w.size(), nullptr, 0, nullptr, nullptr);
	std::string s(len, '\0');
	WideCharToMultiByte(CP_UTF8, 0, w.c_str(), (int)w.size(), &s[0], len, nullptr, nullptr);
	return s;
}

void WriteStatus(const char* state)
{
	Value s = Value::object();
	s.set("state", state);
	s.set("implementation", "native");
	s.set("pid", (double)GetCurrentProcessId());
	s.set("thread", (double)GetCurrentThreadId());
	s.set("ticks", (double)g_ticks);
	s.set("handled", (double)g_handled);
	s.set("time", (double)time(nullptr));
	s.set("comm_dir", Narrow(CommDir()));
	if (!g_lastError.empty())
		s.set("last_error", g_lastError);
	WriteAtomic(CommFile(L"status.json"), mj::dump(s));
}

// ---------------------------------------------------------------------------
// Helpers

Value ParseOrRaw(const std::string& text)
{
	if (text.empty())
		return Value();
	try
	{
		return mj::parse(text);
	}
	catch (...)
	{
		Value v = Value::object();
		v.set("raw", text);
		return v;
	}
}

Value ExportResult(const std::vector<std::string>& files, const std::string& requested, const char* format)
{
	Value r = Value::object();
	r.set("exported", !files.empty());
	r.set("file_paths", files.empty() ? Value::from(std::vector<std::string>{requested}) : Value::from(files));
	r.set("format", format);
	return r;
}

Marvelous::ImportExportOption ExportOptions(const Value& p)
{
	Marvelous::ImportExportOption opt;
	const Value* o = p.find("options");
	if (!o || o->type != Value::Object)
		return opt;
	opt.bExportGarment = o->boolean("bExportGarment", opt.bExportGarment);
	opt.bExportAvatar = o->boolean("bExportAvatar", opt.bExportAvatar);
	opt.bSingleObject = o->boolean("bSingleObject", opt.bSingleObject);
	opt.bThin = o->boolean("bThin", opt.bThin);
	opt.bUnifiedUVCoordinates = o->boolean("bUnifiedUVCoordinates", opt.bUnifiedUVCoordinates);
	opt.bCreateUnifiedTexture = o->boolean("bCreateUnifiedTexture", opt.bCreateUnifiedTexture);
	opt.bIncludeHiddenObject = o->boolean("bIncludeHiddenObject", opt.bIncludeHiddenObject);
	opt.bIncludeInnerShape = o->boolean("bIncludeInnerShape", opt.bIncludeInnerShape);
	opt.scale = (float)o->num("scale", opt.scale);
	return opt;
}

unsigned int U(const Value& p, const char* key) { return (unsigned int)p.num(key); }
int I(const Value& p, const char* key) { return (int)p.num(key); }
int I(const Value& p, const char* key, int def) { return (int)p.num(key, def); }

// ---------------------------------------------------------------------------
// Command handlers (same names, params and results as plugin/clo3d_mcp_plugin.py)

typedef std::function<Value(const Value&)> Handler;

std::map<std::string, Handler> BuildHandlers()
{
	std::map<std::string, Handler> h;

	h["ping"] = [](const Value&) {
		Value r = Value::object();
		r.set("pong", true);
		r.set("in_clo3d", true);
		r.set("implementation", "native");
		return r;
	};

	// -- Scene --
	h["get_project_info"] = [](const Value&) {
		Value r = Value::object();
		r.set("project_name", UTILITY_API->GetProjectName());
		r.set("project_path", UTILITY_API->GetProjectFilePath());
		r.set("clo_version", std::to_string(UTILITY_API->GetMajorVersion()) + "." +
								 std::to_string(UTILITY_API->GetMinorVersion()) + "." +
								 std::to_string(UTILITY_API->GetPatchVersion()));
		r.set("pattern_count", PATTERN_API->GetPatternCount());
		r.set("fabric_count", FABRIC_API->GetFabricCount(true));
		r.set("colorway_count", UTILITY_API->GetColorwayCount());
		return r;
	};
	h["new_project"] = [](const Value&) {
		UTILITY_API->NewProject();
		return Value::object().set("created", true);
	};
	h["open_file"] = [](const Value& p) {
		std::string path = p.str("file_path");
		bool ok = IMPORT_API->ImportFile(path);
		return Value::object().set("opened", ok).set("file_path", path);
	};
	h["save_file"] = [](const Value& p) {
		std::string path = p.str("file_path");
		std::string saved = EXPORT_API->ExportZPrj(path);
		return Value::object().set("saved", !saved.empty()).set("file_path", saved.empty() ? path : saved);
	};
	h["get_garment_info"] = [](const Value&) {
		return Value::object().set("garment_info", ParseOrRaw(EXPORT_API->ExportGarmentInformationToStream()));
	};

	// -- Pattern --
	h["get_pattern_count"] = [](const Value&) {
		return Value::object().set("count", PATTERN_API->GetPatternCount());
	};
	h["get_pattern_list"] = [](const Value&) {
		int count = PATTERN_API->GetPatternCount();
		Value list = Value::array();
		for (int i = 0; i < count; ++i)
			list.a.push_back(Value::object().set("index", i).set("name", PATTERN_API->GetPatternPieceName(i)));
		return Value::object().set("patterns", list).set("count", count);
	};
	h["get_pattern_info"] = [](const Value& p) {
		int index = I(p, "pattern_index");
		return Value::object()
			.set("index", index)
			.set("name", PATTERN_API->GetPatternPieceName(index))
			.set("info", ParseOrRaw(PATTERN_API->GetPatternInformation(index)));
	};
	h["get_bounding_box"] = [](const Value& p) {
		int index = I(p, "pattern_index");
		return Value::object().set("index", index).set("bounding_box", Value::from(PATTERN_API->GetBoundingBoxOfPattern(index)));
	};
	h["set_pattern_name"] = [](const Value& p) {
		int index = I(p, "pattern_index");
		std::string name = p.str("name");
		PATTERN_API->SetPatternPieceName(index, name);
		return Value::object().set("index", index).set("name", name);
	};
	h["copy_pattern"] = [](const Value& p) {
		int index = I(p, "pattern_index");
		float x = (float)p.num("x", 0), y = (float)p.num("y", 0);
		int created = PATTERN_API->CopyPatternPiecePos(index, x, y);
		Value pos = Value::array();
		pos.a.push_back(x);
		pos.a.push_back(y);
		return Value::object().set("copied", created >= 0).set("source_index", index).set("new_index", created).set("position", pos);
	};
	h["delete_pattern"] = [](const Value& p) {
		int index = I(p, "pattern_index");
		PATTERN_API->DeletePatternPiece(index);
		return Value::object().set("deleted", true).set("index", index);
	};
	h["flip_pattern"] = [](const Value& p) {
		int index = I(p, "pattern_index");
		bool horizontal = p.boolean("horizontal", true), each = p.boolean("each", true);
		PATTERN_API->FlipPatternPiece(index, horizontal, each);
		return Value::object().set("flipped", true).set("index", index).set("horizontal", horizontal).set("each", each);
	};
	h["create_pattern"] = [](const Value& p) {
		const Value* pts = p.find("points");
		if (!pts || pts->type != Value::Array)
			throw std::runtime_error("missing array parameter 'points'");
		std::vector<std::tuple<float, float, int>> points;
		for (const Value& pt : pts->a)
		{
			if (pt.type != Value::Array || pt.a.size() < 2)
				throw std::runtime_error("each point must be [x, y] or [x, y, type]");
			int vtype = pt.a.size() > 2 ? (int)pt.a[2].n : 0;
			points.emplace_back((float)pt.a[0].n, (float)pt.a[1].n, vtype);
		}
		int result = PATTERN_API->CreatePatternWithPoints(points);
		return Value::object().set("created", true).set("point_count", (int)points.size()).set("result", result);
	};
	h["get_arrangement_list"] = [](const Value&) {
		return Value::object().set("arrangements", Value::from(PATTERN_API->GetArrangementList()));
	};

	// -- Fabric --
	h["get_fabric_count"] = [](const Value&) {
		return Value::object().set("count", FABRIC_API->GetFabricCount(true));
	};
	h["get_fabric_list"] = [](const Value&) {
		unsigned int count = FABRIC_API->GetFabricCount(true);
		Value list = Value::array();
		for (unsigned int i = 0; i < count; ++i)
			list.a.push_back(Value::object().set("index", i));
		return Value::object().set("fabrics", list).set("count", count);
	};
	h["add_fabric"] = [](const Value& p) {
		std::string path = p.str("file_path");
		unsigned int index = FABRIC_API->AddFabric(path);
		return Value::object().set("added", true).set("fabric_index", index).set("file_path", path);
	};
	h["replace_fabric"] = [](const Value& p) {
		int fabric = I(p, "fabric_index");
		std::string path = p.str("file_path");
		bool ok = FABRIC_API->ReplaceFabric(fabric, path);
		return Value::object().set("replaced", ok).set("fabric_index", fabric).set("file_path", path);
	};
	h["assign_fabric"] = [](const Value& p) {
		unsigned int fabric = U(p, "fabric_index"), pattern = U(p, "pattern_index");
		bool ok = FABRIC_API->AssignFabricToPattern(fabric, pattern, I(p, "assign_option", 1));
		return Value::object().set("assigned", ok).set("fabric_index", fabric).set("pattern_index", pattern);
	};
	h["set_fabric_color"] = [](const Value& p) {
		unsigned int fabric = U(p, "fabric_index");
		int r = I(p, "r", 255), g = I(p, "g", 255), b = I(p, "b", 255), a = I(p, "a", 255);
		bool ok = FABRIC_API->SetFabricPBRMaterialBaseColor(fabric, (unsigned int)I(p, "material_face", 0),
															r / 255.0f, g / 255.0f, b / 255.0f, a / 255.0f);
		Value color = Value::array();
		for (int c : {r, g, b, a})
			color.a.push_back(c);
		return Value::object().set("set", ok).set("fabric_index", fabric).set("color", color);
	};
	h["get_fabric_for_pattern"] = [](const Value& p) {
		int pattern = I(p, "pattern_index");
		return Value::object().set("pattern_index", pattern).set("fabric_index", FABRIC_API->GetFabricIndexForPattern(pattern));
	};
	h["delete_fabric"] = [](const Value& p) {
		unsigned int fabric = U(p, "fabric_index");
		return Value::object().set("deleted", FABRIC_API->DeleteFabric(fabric)).set("fabric_index", fabric);
	};

	// -- Export --
	h["export_obj"] = [](const Value& p) {
		std::string path = p.str("file_path");
		return ExportResult(EXPORT_API->ExportOBJ(path, ExportOptions(p)), path, "obj");
	};
	h["export_fbx"] = [](const Value& p) {
		std::string path = p.str("file_path");
		return ExportResult(EXPORT_API->ExportFBX(path, ExportOptions(p)), path, "fbx");
	};
	h["export_glb"] = [](const Value& p) {
		std::string path = p.str("file_path");
		return ExportResult(EXPORT_API->ExportGLB(path, ExportOptions(p)), path, "glb");
	};
	h["export_gltf"] = [](const Value& p) {
		std::string path = p.str("file_path");
		return ExportResult(EXPORT_API->ExportGLTF(path, ExportOptions(p), false), path, "gltf");
	};
	h["export_thumbnail"] = [](const Value& p) {
		std::string path = p.str("file_path");
		std::string out = EXPORT_API->ExportThumbnail3D(path);
		return Value::object().set("exported", !out.empty()).set("file_path", out.empty() ? path : out);
	};
	h["export_snapshot"] = [](const Value& p) {
		std::string path = p.str("file_path");
		std::vector<std::vector<std::string>> out = EXPORT_API->ExportSnapshot3D(path);
		Value files = Value::array();
		for (const auto& group : out)
			files.a.push_back(Value::from(group));
		return Value::object().set("exported", !out.empty()).set("file_path", path).set("file_paths", files);
	};
	h["export_turntable"] = [](const Value& p) {
		std::string path = p.str("file_path");
		int count = I(p, "number_of_images", 36), w = I(p, "width", 2500), hgt = I(p, "height", 2500);
		std::vector<std::string> out = EXPORT_API->ExportTurntableImages(path, count, w, hgt);
		return Value::object()
			.set("exported", !out.empty())
			.set("file_path", path)
			.set("file_paths", Value::from(out))
			.set("number_of_images", count)
			.set("width", w)
			.set("height", hgt);
	};
	h["export_tech_pack"] = [](const Value& p) {
		std::string path = p.str("file_path");
		Marvelous::ExportTechpackOption opt;
		EXPORT_API->ExportTechPack(path, opt);
		return Value::object().set("exported", Exists(std::wstring(path.begin(), path.end()))).set("file_path", path);
	};

	// -- Import --
	h["import_file"] = [](const Value& p) {
		std::string path = p.str("file_path");
		return Value::object().set("imported", IMPORT_API->ImportFile(path)).set("file_path", path);
	};
	h["import_avatar"] = [](const Value& p) {
		std::string path = p.str("file_path");
		std::string apf = p.str("apf_path", "");
		return Value::object().set("imported", IMPORT_API->ImportAVAC(path, apf)).set("file_path", path);
	};
	h["import_fabric"] = [](const Value& p) {
		std::string path = p.str("file_path");
		unsigned int index = FABRIC_API->AddFabric(path);
		return Value::object().set("imported", true).set("fabric_index", index).set("file_path", path);
	};

	// -- Simulation --
	h["simulate"] = [](const Value& p) {
		unsigned int steps = (unsigned int)p.num("steps", 100);
		bool ok = UTILITY_API->Simulate(steps);
		return Value::object().set("simulated", ok).set("steps", steps);
	};
	h["set_simulation_quality"] = [](const Value& p) {
		int quality = I(p, "quality");
		int mode = I(p, "simulation_mode", quality == 3 ? 1 : 0);
		UTILITY_API->SetSimulationQuality(quality, mode);
		return Value::object().set("quality", quality).set("simulation_mode", mode);
	};

	// -- Colorway --
	h["get_colorways"] = [](const Value&) {
		unsigned int count = UTILITY_API->GetColorwayCount();
		unsigned int current = UTILITY_API->GetCurrentColorwayIndex();
		std::vector<std::string> names = EXPORT_API->GetColorwayNameList();
		Value list = Value::array();
		for (unsigned int i = 0; i < count; ++i)
		{
			Value c = Value::object().set("index", i);
			if (i < names.size())
				c.set("name", names[i]);
			c.set("current", i == current);
			list.a.push_back(c);
		}
		return Value::object().set("colorways", list).set("count", count).set("current_index", current);
	};
	h["set_current_colorway"] = [](const Value& p) {
		unsigned int index = U(p, "colorway_index");
		UTILITY_API->SetCurrentColorwayIndex(index);
		return Value::object().set("set", true).set("colorway_index", index);
	};
	h["set_colorway_name"] = [](const Value& p) {
		unsigned int index = U(p, "colorway_index");
		std::string name = p.str("name");
		UTILITY_API->SetColorwayName(index, name);
		return Value::object().set("set", true).set("colorway_index", index).set("name", name);
	};
	h["copy_colorway"] = [](const Value& p) {
		unsigned int index = U(p, "colorway_index");
		unsigned int created = UTILITY_API->CopyColorway(index, I(p, "copy_option", 0));
		return Value::object().set("copied", true).set("source_index", index).set("new_index", created);
	};
	h["delete_colorway"] = [](const Value& p) {
		unsigned int index = U(p, "colorway_index");
		UTILITY_API->DeleteColorwayItem(index);
		return Value::object().set("deleted", true).set("colorway_index", index);
	};

	// -- Avatar --
	h["get_avatars"] = [](const Value&) {
		unsigned int count = EXPORT_API->GetAvatarCount();
		std::vector<std::string> names = EXPORT_API->GetAvatarNameList();
		std::vector<int> genders = EXPORT_API->GetAvatarGenderList();
		Value list = Value::array();
		for (size_t i = 0; i < names.size(); ++i)
		{
			Value a = Value::object().set("index", (int)i).set("name", names[i]);
			if (i < genders.size())
				a.set("gender", genders[i]);
			list.a.push_back(a);
		}
		return Value::object().set("avatars", list).set("count", count);
	};
	h["show_hide_avatar"] = [](const Value& p) {
		bool show = p.boolean("show", true);
		UTILITY_API->SetShowHideAvatar(show);
		return Value::object().set("visible", show);
	};
	h["get_avatar_genders"] = [](const Value&) {
		return Value::object().set("genders", Value::from(EXPORT_API->GetAvatarGenderList()));
	};

	return h;
}

const std::map<std::string, Handler>& Handlers()
{
	static const std::map<std::string, Handler> handlers = BuildHandlers();
	return handlers;
}

// Runs a handler, turning C++ exceptions and hardware faults into an error message
// instead of taking CLO down (built with /EHa so catch(...) sees SEH faults).
std::string RunCommand(const std::string& data)
{
	Value response = Value::object();
	Value request;
	try
	{
		request = mj::parse(data);
	}
	catch (const std::exception& e)
	{
		response.set("id", Value()).set("status", "error").set("message", e.what());
		return mj::dump(response);
	}

	const Value* id = request.find("id");
	response.set("id", id ? *id : Value());
	std::string type = request.str("type", "");
	const Value* params = request.find("params");
	Value p = (params && params->type == Value::Object) ? *params : Value::object();

	auto it = Handlers().find(type);
	if (it == Handlers().end())
	{
		response.set("status", "error").set("message", "Unknown command: " + type);
		return mj::dump(response);
	}

	try
	{
		Value result = it->second(p);
		response.set("status", "success").set("result", result);
	}
	catch (const std::exception& e)
	{
		response.set("status", "error").set("message", std::string(e.what()));
	}
	catch (...)
	{
		response.set("status", "error").set("message", "CLO raised a native exception while running '" + type + "'");
	}
	return mj::dump(response);
}

// ---------------------------------------------------------------------------
// Listener

void StopListener(const char* reason);

void ServeOnce()
{
	std::wstring requestFile = CommFile(L"request.json");
	if (!Exists(requestFile))
		return;

	std::string data;
	if (!ReadAll(requestFile, data))
		return; // still being renamed into place, try on the next tick
	DeleteFileW(requestFile.c_str());
	if (data.find_first_not_of(" \r\n\t") == std::string::npos)
		return;

	std::string response = RunCommand(data);
	if (!WriteAtomic(CommFile(L"response.json"), response))
		g_lastError = "could not write response.json";
	++g_handled;
}

LRESULT CALLBACK WindowProc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam)
{
	if (msg == WM_TIMER && wParam == TIMER_ID)
	{
		++g_ticks;
		if (g_busy) // a long CLO operation is pumping messages; don't re-enter
			return 0;
		g_busy = true;
		if (Exists(CommFile(L"stop")))
		{
			DeleteFileW(CommFile(L"stop").c_str());
			g_busy = false;
			StopListener("stop file");
			return 0;
		}
		ServeOnce();
		DWORD now = GetTickCount();
		if (now - g_lastStatus > 1000)
		{
			WriteStatus("listening");
			g_lastStatus = now;
		}
		g_busy = false;
		return 0;
	}
	if (msg == WM_STOP_LISTENER)
	{
		StopListener("menu");
		return 0;
	}
	return DefWindowProcW(hwnd, msg, wParam, lParam);
}

// The listener window of any copy of this DLL loaded in this process. FindWindowEx searches
// message-only windows of every process, so skip other CLO instances' listeners.
HWND FindListener()
{
	HWND w = nullptr;
	while ((w = FindWindowExW(HWND_MESSAGE, w, WINDOW_CLASS, nullptr)) != nullptr)
	{
		DWORD pid = 0;
		GetWindowThreadProcessId(w, &pid);
		if (pid == GetCurrentProcessId())
			return w;
	}
	return nullptr;
}

bool StartListener()
{
	if (g_window || FindListener())
		return true;

	// CLO loads a plug-in DLL to read its menu name and may unload it again. The timer
	// calls into this DLL, so pin it for the life of the process before creating it.
	HINSTANCE instance = nullptr;
	if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_PIN,
							(LPCWSTR)&WindowProc, &instance))
	{
		g_lastError = "could not pin plug-in DLL: " + std::to_string(GetLastError());
		WriteStatus("failed");
		return false;
	}

	WNDCLASSEXW wc = {};
	wc.cbSize = sizeof(wc);
	wc.lpfnWndProc = WindowProc;
	wc.hInstance = instance;
	wc.lpszClassName = WINDOW_CLASS;
	RegisterClassExW(&wc); // fails harmlessly if already registered

	g_window = CreateWindowExW(0, WINDOW_CLASS, L"CLO MCP", 0, 0, 0, 0, 0, HWND_MESSAGE, nullptr, instance, nullptr);
	if (!g_window)
	{
		g_lastError = "CreateWindowEx failed: " + std::to_string(GetLastError());
		WriteStatus("failed");
		return false;
	}

	CreateDirectoryW(CommDir().c_str(), nullptr);
	for (const wchar_t* name : {L"request.json", L"response.json", L"stop"})
		DeleteFileW(CommFile(name).c_str());

	SetTimer(g_window, TIMER_ID, TIMER_MS, nullptr);
	g_lastError.clear();
	WriteStatus("listening");
	return true;
}

void StopListener(const char* reason)
{
	if (!g_window)
		return;
	KillTimer(g_window, TIMER_ID);
	DestroyWindow(g_window);
	g_window = nullptr;
	WriteStatus((std::string("stopped: ") + reason).c_str());
}

// ---------------------------------------------------------------------------
// CLO plug-in entry points

class CloMcpPlugin : public CLOAPI::LibraryWindowInterface
{
public:
	bool IsPluginEnabled() override { return true; }

	void DoFunctionStartUp() override { StartListener(); }

	// Plugins > Plug-in > "CLO MCP Listener (start/stop)"
	void DoFunction() override
	{
		if (HWND running = FindListener())
		{
			SendMessageW(running, WM_STOP_LISTENER, 0, 0);
			UTILITY_API->DisplayMessageBox("CLO MCP listener stopped.");
		}
		else if (StartListener())
			UTILITY_API->DisplayMessageBox("CLO MCP listener running.\nFolder: " + Narrow(CommDir()));
		else
			UTILITY_API->DisplayMessageBox("CLO MCP listener failed to start: " + g_lastError);
	}

	const char* GetActionName() override { return "CLO MCP Listener (start/stop)"; }
	const char* GetObjectNameTreeToAddAction() override { return "menuPlugins / menuPlug_In"; }
	int GetPositionIndexToAddAction() override { return 1; }
};
} // namespace

// Library-window plug-in entry point (CloLibraryAPI_Plugin.dll in the Plugins folder).
extern "C" __declspec(dllexport) CLOAPI::LibraryWindowInterface* Create()
{
	StartListener(); // CLO creates plug-ins on its UI thread when it loads them
	return new CloMcpPlugin();
}

// General API plug-in entry points (registered through Plugin tab > Plug-in Manager),
// same exports as the SDK's ExportPlugin sample.
extern "C" __declspec(dllexport) void DoFunction()
{
	CloMcpPlugin().DoFunction();
}

extern "C" __declspec(dllexport) void DoFunctionAfterLoadingCLOFile(const char*)
{
}

extern "C" __declspec(dllexport) const char* GetActionName()
{
	// CLO queries this on its UI thread when it builds the plug-in menu at startup,
	// which is the only call a general plug-in gets before a click: start listening then.
	StartListener();
	return "CLO MCP Listener (start/stop)";
}

extern "C" __declspec(dllexport) const char* GetObjectNameTreeToAddAction()
{
	return "menuPlugins / menuPlug_In";
}

extern "C" __declspec(dllexport) int GetPositionIndexToAddAction()
{
	return 1;
}
