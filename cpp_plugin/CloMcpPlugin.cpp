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

void WriteStatus(const char* state, const Value& extra = Value())
{
	Value s = Value::object();
	s.set("state", state);
	for (const auto& kv : extra.o)
		s.set(kv.first, kv.second);
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

// CLO does not validate indices and can crash on bad ones, so every index is checked first.
int PatternIndex(const Value& p, const char* key = "pattern_index")
{
	int index = I(p, key);
	int count = PATTERN_API->GetPatternCount();
	if (index < 0 || index >= count)
		throw std::runtime_error(std::string(key) + " " + std::to_string(index) + " is out of range (" +
								 std::to_string(count) + " patterns)");
	return index;
}

// Number of lines in a pattern's outline (child == -1) or in one of its internal shapes.
// GetLineLength returns 0 past the last line.
int LineCount(int pattern, int child = -1)
{
	int n = 0;
	while (n < 2000 && (child < 0 ? PATTERN_API->GetLineLength(pattern, n) : PATTERN_API->GetLineLength(pattern, child, n)) > 0.0f)
		++n;
	return n;
}

int LineIndex(const Value& p, int pattern, const char* key = "line_index", bool allowAll = false)
{
	int line = I(p, key, allowAll ? -1 : -2);
	if (allowAll && line == -1)
		return -1;
	int count = LineCount(pattern);
	if (line < 0 || line >= count)
		throw std::runtime_error(std::string(key) + " " + std::to_string(line) + " is out of range (pattern " +
								 std::to_string(pattern) + " has " + std::to_string(count) + " outline lines)");
	return line;
}

// Number of internal shapes (children) of a pattern: a child exists while its line 0 has length.
int ChildCount(int pattern)
{
	int n = 0;
	while (n < 2000 && PATTERN_API->GetLineLength(pattern, n, 0) > 0.0f)
		++n;
	return n;
}

int RequireCreated(int pattern, int before, const char* hint)
{
	int created = ChildCount(pattern) - before;
	if (created <= 0)
		throw std::runtime_error(std::string("CLO did not create any internal line; ") + hint);
	return created;
}

std::vector<std::tuple<float, float, int>> Points(const Value& p)
{
	const Value* pts = p.find("points");
	if (!pts || pts->type != Value::Array || pts->a.size() < 2)
		throw std::runtime_error("'points' must be a list of at least two [x, y] or [x, y, type] entries");
	std::vector<std::tuple<float, float, int>> points;
	for (const Value& pt : pts->a)
	{
		if (pt.type != Value::Array || pt.a.size() < 2)
			throw std::runtime_error("each point must be [x, y] or [x, y, type]");
		points.emplace_back((float)pt.a[0].n, (float)pt.a[1].n, pt.a.size() > 2 ? (int)pt.a[2].n : 0);
	}
	return points;
}

std::wstring Widen(const std::string& s)
{
	if (s.empty())
		return L"";
	int len = MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), nullptr, 0);
	std::wstring w(len, L'\0');
	MultiByteToWideChar(CP_UTF8, 0, s.c_str(), (int)s.size(), &w[0], len);
	return w;
}

// ---------------------------------------------------------------------------
// Command handlers (same names, params and results as plugin/clo3d_mcp_plugin.py)

typedef std::function<Value(const Value&)> Handler;

// GetFabricCount(true) returned 0 in CLO 2025.2.236 with fabrics in the scene; take the
// larger count and, failing that, count fabrics by name.
unsigned int FabricCount()
{
	unsigned int count = FABRIC_API->GetFabricCount(true);
	unsigned int all = FABRIC_API->GetFabricCount(false);
	if (all > count) count = all;
	if (count == 0)
		while (count < 1024 && !FABRIC_API->GetFabricName((int)count).empty())
			++count;
	return count;
}

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
		r.set("fabric_count", FabricCount());
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
		return Value::object().set("count", FabricCount());
	};
	h["get_fabric_list"] = [](const Value&) {
		unsigned int count = FabricCount();
		Value list = Value::array();
		for (unsigned int i = 0; i < count; ++i)
			list.a.push_back(Value::object().set("index", i).set("name", FABRIC_API->GetFabricName((int)i)));
		return Value::object().set("fabrics", list).set("count", count);
	};
	h["get_fabric_info"] = [](const Value& p) {
		int fabric = I(p, "fabric_index");
		Value info = Value::object();
		for (const auto& kv : FABRIC_API->GetFabricInformation(fabric))
			info.set(kv.first, kv.second);
		return Value::object().set("fabric_index", fabric).set("name", FABRIC_API->GetFabricName(fabric))
			.set("information", info).set("fabric_info_json", FABRIC_API->GetFabricInfo(fabric));
	};
	h["set_fabric_information"] = [](const Value& p) {
		int fabric = I(p, "fabric_index");
		std::map<std::string, std::string> info;
		if (p.has("information"))
			for (const auto& kv : p.find("information")->o)
				info[kv.first] = kv.second.s;
		if (!info.empty())
			FABRIC_API->SetFabricInformation(fabric, info);
		if (p.has("name"))
			FABRIC_API->SetFabricName((unsigned int)fabric, p.str("name"));
		Value now = Value::object();
		for (const auto& kv : FABRIC_API->GetFabricInformation(fabric))
			now.set(kv.first, kv.second);
		return Value::object().set("fabric_index", fabric).set("name", FABRIC_API->GetFabricName(fabric)).set("information", now);
	};
	h["export_fabric"] = [](const Value& p) {
		int fabric = I(p, "fabric_index");
		std::string path = FABRIC_API->ExportFabric(p.str("file_path"), fabric);
		return Value::object().set("fabric_index", fabric).set("file_path", path).set("exported", !path.empty());
	};
	h["change_fabric_with_json"] = [](const Value& p) {
		unsigned int fabric = U(p, "fabric_index");
		std::string path = p.str("file_path");
		return Value::object().set("fabric_index", fabric).set("changed", FABRIC_API->ChangeFabricWithJson(fabric, path));
	};
	h["export_pattern_json"] = [](const Value& p) {
		std::string path = p.str("file_path");
		return Value::object().set("file_path", path).set("exported", PATTERN_API->ExportPatternJSON(path));
	};
	h["import_pattern_json"] = [](const Value& p) {
		std::string path = p.str("file_path");
		return Value::object().set("file_path", path).set("imported", PATTERN_API->ImportPatternJSON(path));
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

	// -- Visual feedback --
	// ExportSnapshot3D opens CLO's snapshot dialog, so captures use ExportThumbnail3D (no dialog)
	// after pointing the camera. Camera indices from the SDK: 0 bottom, 1 3/4 right, 2 front,
	// 3 3/4 left, 4 right, 5 top, 6 left, 7 focus zoom, 8 back, 9 zoom extents all.
	h["capture_3d"] = [](const Value& p) {
		int camera = I(p, "camera", -1);
		if (camera < -1 || camera > 9)
			throw std::runtime_error("camera must be 0-9, or -1 to keep the current view");
		if (camera >= 0)
			UTILITY_API->SetCamViewPoint(camera);
		std::string path = p.str("file_path");
		std::string out = EXPORT_API->ExportThumbnail3D(path);
		if (out.empty())
			throw std::runtime_error("CLO did not export the 3D view");
		return Value::object().set("file_path", out).set("camera", camera);
	};
	h["set_fit_map"] = [](const Value& p) {
		std::string mode = p.str("mode");
		if (mode != "off" && mode != "strain" && mode != "stress")
			throw std::runtime_error("mode must be \"off\", \"strain\" or \"stress\"");
		UTILITY_API->SetStrainMapStatus(mode == "strain");
		UTILITY_API->SetStressMapStatus(mode == "stress");
		return Value::object()
			.set("strain_map", UTILITY_API->GetStrainMapStatus())
			.set("stress_map", UTILITY_API->GetStressMapStatus());
	};
	h["get_fit_map"] = [](const Value&) {
		return Value::object()
			.set("strain_map", UTILITY_API->GetStrainMapStatus())
			.set("stress_map", UTILITY_API->GetStressMapStatus());
	};
	// ExportSnapshot3D opens CLO's snapshot dialog, which would block the listener until a person
	// closes it, so snapshots are taken per camera view without a dialog.
	h["export_snapshot"] = [](const Value& p) {
		std::string path = p.str("file_path");
		std::string base = path.size() > 4 && path.substr(path.size() - 4) == ".png" ? path.substr(0, path.size() - 4) : path;
		const std::pair<const char*, int> views[] = {{"front", 2}, {"back", 8}, {"left", 6}, {"right", 4}};
		Value files = Value::array();
		for (const auto& view : views)
		{
			UTILITY_API->SetCamViewPoint(view.second);
			std::string out = EXPORT_API->ExportThumbnail3D(base + "_" + view.first + ".png");
			if (!out.empty())
				files.a.push_back(out);
		}
		UTILITY_API->SetCamViewPoint(2);
		return Value::object().set("exported", !files.a.empty()).set("file_paths", files);
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
	// .avac goes through ImportAVAC; everything else (.avt, ...) through the generic importer,
	// which is what loads an avatar with its arrangement points (verified with CLO's .avt files).
	h["import_avatar"] = [](const Value& p) {
		std::string path = p.str("file_path");
		std::string ext = path.size() > 5 ? path.substr(path.size() - 5) : path;
		for (char& ch : ext)
			ch = (char)tolower((unsigned char)ch);
		bool ok = ext == ".avac" ? IMPORT_API->ImportAVAC(path, p.str("apf_path", "")) : IMPORT_API->ImportFile(path);
		return Value::object().set("imported", ok).set("file_path", path).set("avatar_count", EXPORT_API->GetAvatarCount());
	};

	// -- Collision objects --
	// ImportOBJ with explicit options never shows the import dialog. As an avatar (type 0) the
	// mesh becomes something cloth collides with; bAutoTranslate=false keeps the file's own
	// coordinates (mm), so generated rods land exactly where they were placed.
	h["import_obj"] = [](const Value& p) {
		std::string path = p.str("file_path");
		int type = I(p, "object_type", 0);
		if (type < 0 || type > 2)
			throw std::runtime_error("object_type must be 0 (avatar/collision), 1 (trim) or 2 (garment)");
		Marvelous::ImportExportOption opt;
		opt.ImportObjectType = type;
		opt.bAutoTranslate = p.boolean("align_to_ground", false);
		opt.scale = (float)p.num("scale", 1.0);
		unsigned int before = EXPORT_API->GetAvatarCount();
		bool ok = IMPORT_API->ImportOBJ(path, opt);
		return Value::object().set("imported", ok).set("file_path", path).set("object_type", type)
			.set("avatar_count_before", before).set("avatar_count", EXPORT_API->GetAvatarCount());
	};
	// Per-pattern mesh counts as CLO reports them (raw strings), plus the total cloth vertex
	// count, so the server can check whether GetClothPositions lists patterns in order.
	h["get_mesh_counts"] = [](const Value&) {
		Value list = Value::array();
		for (int i = 0, n = PATTERN_API->GetPatternCount(); i < n; ++i)
			list.a.push_back(Value::from(PATTERN_API->GetMeshCountByType(i)));
		std::vector<float> positions;
		UTILITY_API->GetClothPositions(positions);
		return Value::object().set("patterns", list).set("total_vertices", (double)(positions.size() / 3));
	};
	// Remove collision objects / avatars by index (e.g. a temporary guide rod).
	h["delete_objects"] = [](const Value& p) {
		const Value* list = p.find("indices");
		if (!list || list->type != Value::Array || list->a.empty())
			throw std::runtime_error("'indices' must list the object (avatar) indices to delete");
		int count = (int)EXPORT_API->GetAvatarCount();
		std::vector<int> indices;
		for (const Value& v : list->a)
		{
			int index = (int)v.n;
			if (index < 0 || index >= count)
				throw std::runtime_error("object index " + std::to_string(index) + " is out of range (" +
										 std::to_string(count) + " objects)");
			indices.push_back(index);
		}
		bool ok = UTILITY_API->DeleteAvatar(indices);
		return Value::object().set("deleted", ok).set("object_count", EXPORT_API->GetAvatarCount());
	};
	// Bounding box of simulated cloth in 3D (mm), from GetClothPositions; optionally only the
	// vertices inside a region box (e.g. a slice through the middle of a pocket).
	h["get_cloth_bounds"] = [](const Value& p) {
		float rmin[3] = {-1e9f, -1e9f, -1e9f}, rmax[3] = {1e9f, 1e9f, 1e9f};
		const Value* regionMin = p.find("min");
		const Value* regionMax = p.find("max");
		for (int k = 0; k < 3; ++k)
		{
			if (regionMin && regionMin->type == Value::Array && regionMin->a.size() == 3)
				rmin[k] = (float)regionMin->a[k].n;
			if (regionMax && regionMax->type == Value::Array && regionMax->a.size() == 3)
				rmax[k] = (float)regionMax->a[k].n;
		}
		std::vector<float> positions;
		UTILITY_API->GetClothPositions(positions);
		size_t total = positions.size() / 3, n = 0;
		// optional [first, count] slice of the vertex list (the server maps patterns to slices)
		size_t first = 0, last = total;
		const Value* range = p.find("vertex_range");
		if (range && range->type == Value::Array && range->a.size() == 2)
		{
			first = (size_t)range->a[0].n;
			last = first + (size_t)range->a[1].n;
			if (last > total)
				throw std::runtime_error("vertex_range ends at " + std::to_string(last) + " but CLO reports " +
										 std::to_string(total) + " cloth vertices");
		}
		float lo[3] = {1e9f, 1e9f, 1e9f}, hi[3] = {-1e9f, -1e9f, -1e9f};
		for (size_t i = first; i < last; ++i)
		{
			const float* v = &positions[i * 3];
			if (v[0] < rmin[0] || v[0] > rmax[0] || v[1] < rmin[1] || v[1] > rmax[1] || v[2] < rmin[2] || v[2] > rmax[2])
				continue;
			++n;
			for (int k = 0; k < 3; ++k)
			{
				lo[k] = v[k] < lo[k] ? v[k] : lo[k];
				hi[k] = v[k] > hi[k] ? v[k] : hi[k];
			}
		}
		if (n == 0)
			return Value::object().set("vertex_count", 0).set("total_vertices", (double)total);
		Value mn = Value::array(), mx = Value::array();
		for (int k = 0; k < 3; ++k)
		{
			mn.a.push_back(lo[k]);
			mx.a.push_back(hi[k]);
		}
		return Value::object().set("vertex_count", (double)n).set("total_vertices", (double)total).set("min", mn).set("max", mx);
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

	// -- Geometry --
	// Full pattern export (outlines, internal shapes, notches, seams) plus exact line lengths,
	// which the server turns into line-indexed pieces and seams.
	h["get_pattern_geometry"] = [](const Value&) {
		std::string path = Narrow(CommFile(L"pattern_export.json"));
		if (!PATTERN_API->ExportPatternJSON(path))
			throw std::runtime_error("ExportPatternJSON failed");
		std::string text;
		bool read = ReadAll(Widen(path), text);
		DeleteFileW(Widen(path).c_str());
		if (!read)
			throw std::runtime_error("could not read the pattern export");
		Value exported = mj::parse(text);
		Value lengths = Value::array();
		int count = PATTERN_API->GetPatternCount();
		for (int i = 0; i < count; ++i)
		{
			Value piece = Value::object();
			Value outline = Value::array();
			for (int l = 0, n = LineCount(i); l < n; ++l)
				outline.a.push_back(PATTERN_API->GetLineLength(i, l));
			piece.set("outline", outline);
			Value children = Value::array();
			const Value* list = exported.find("PatternList");
			const Value* internal = (list && i < (int)list->a.size()) ? list->a[i].find("InternalLineList") : nullptr;
			int childCount = internal ? (int)internal->a.size() : 0;
			for (int c = 0; c < childCount; ++c)
			{
				Value lines = Value::array();
				for (int l = 0, n = LineCount(i, c); l < n; ++l)
					lines.a.push_back(PATTERN_API->GetLineLength(i, c, l));
				children.a.push_back(lines);
			}
			piece.set("internal_shapes", children);
			lengths.a.push_back(piece);
		}
		Value seamNames = Value::array();
		for (int s = 0, n = PATTERN_API->GetSeamlinePairGroupCount(); s < n; ++s)
			seamNames.a.push_back(PATTERN_API->GetSeamlinePairGroupName(s));
		return Value::object().set("export", exported).set("line_lengths", lengths).set("seam_names", seamNames);
	};

	// -- Sewing --
	h["sew_lines"] = [](const Value& p) {
		int a = PatternIndex(p, "pattern_a"), b = PatternIndex(p, "pattern_b");
		bool dirA = p.boolean("direction_a", true), dirB = p.boolean("direction_b", true);
		bool childA = p.has("internal_shape_a"), childB = p.has("internal_shape_b");
		int lineA = I(p, "line_a"), lineB = I(p, "line_b");
		int before = PATTERN_API->GetSeamlinePairGroupCount();
		bool ok;
		if (!childA && !childB)
		{
			LineIndex(p, a, "line_a");
			LineIndex(p, b, "line_b");
			ok = PATTERN_API->AddSeamlinePairGroup(a, lineA, b, lineB, dirA, dirB);
		}
		else if (!childA)
		{
			int cB = I(p, "internal_shape_b");
			if (lineB < 0 || lineB >= LineCount(b, cB))
				throw std::runtime_error("line_b is out of range for internal_shape_b");
			LineIndex(p, a, "line_a");
			ok = PATTERN_API->AddSeamlinePairGroup(a, lineA, b, cB, lineB, dirA, dirB);
		}
		else
		{
			int cA = I(p, "internal_shape_a"), cB = I(p, "internal_shape_b", -1);
			if (lineA < 0 || lineA >= LineCount(a, cA))
				throw std::runtime_error("line_a is out of range for internal_shape_a");
			if (lineB < 0 || lineB >= LineCount(b, cB))
				throw std::runtime_error("line_b is out of range");
			ok = PATTERN_API->AddSeamlinePairGroup(a, cA, lineA, b, cB, lineB, dirA, dirB);
		}
		int after = PATTERN_API->GetSeamlinePairGroupCount();
		Value r = Value::object().set("sewn", ok && after > before).set("seam_count", after);
		if (after > before)
			r.set("seam_index", after - 1).set("seam_name", PATTERN_API->GetSeamlinePairGroupName(after - 1));
		return r;
	};
	h["list_topstitch_styles"] = [](const Value&) {
		return Value::object().set("styles", Value::from(PATTERN_API->GetTopstitchStyleList()));
	};
	h["add_topstitch"] = [](const Value& p) {
		int style = I(p, "style_index");
		bool ok;
		if (p.has("seam_index"))
		{
			int seam = I(p, "seam_index");
			if (seam < 0 || seam >= PATTERN_API->GetSeamlinePairGroupCount())
				throw std::runtime_error("seam_index is out of range");
			ok = PATTERN_API->AddSeamlineTopstitch((unsigned int)seam, (float)p.num("start_ratio", 0.0),
												   (float)p.num("end_ratio", 1.0), style);
		}
		else
		{
			int pattern = PatternIndex(p);
			ok = PATTERN_API->AddSegmentTopstitch(pattern, LineIndex(p, pattern), style);
		}
		return Value::object().set("added", ok);
	};
	h["set_seam_taping"] = [](const Value& p) {
		int pattern = PatternIndex(p), line = LineIndex(p, pattern);
		bool on = p.boolean("enabled", true);
		PATTERN_API->SetPatternPieceSeamtaping(pattern, line, on);
		return Value::object().set("pattern_index", pattern).set("line_index", line).set("seam_taping", on);
	};

	// -- Piece state --
	h["set_pattern_state"] = [](const Value& p) {
		int pattern = PatternIndex(p);
		Value r = Value::object().set("pattern_index", pattern);
		if (p.has("frozen")) { bool v = p.boolean("frozen", false); PATTERN_API->SetPatternFreeze(pattern, v); r.set("frozen", v); }
		if (p.has("strengthened")) { bool v = p.boolean("strengthened", false); PATTERN_API->SetPatternStrengthen(pattern, v); r.set("strengthened", v); }
		if (p.has("solidified")) { bool v = p.boolean("solidified", false); PATTERN_API->SetPatternPieceSolidify(pattern, v); r.set("solidified", v); }
		if (p.has("solidify_strength")) { float v = (float)p.num("solidify_strength"); PATTERN_API->SetPatternPieceSolidifyStrengthen(pattern, v); r.set("solidify_strength", v); }
		if (p.has("hidden_3d")) { bool v = p.boolean("hidden_3d", false); PATTERN_API->SetPatternHide3D(pattern, v); r.set("hidden_3d", v); }
		if (p.has("layer")) { int v = I(p, "layer"); PATTERN_API->SetPatternLayer(pattern, v); r.set("layer", v); }
		if (p.has("particle_distance")) { float v = (float)p.num("particle_distance"); PATTERN_API->SetParticleDistanceOfPattern(pattern, v); r.set("particle_distance", v); }
		if (p.has("grain_degrees")) { float v = (float)p.num("grain_degrees"); PATTERN_API->SetPatternPieceGrainDirection(pattern, v); r.set("grain_degrees", v); }
		if (r.o.size() == 1)
			throw std::runtime_error("no state given; pass frozen, strengthened, solidified, solidify_strength, hidden_3d, layer, particle_distance or grain_degrees");
		return r;
	};
	h["get_pattern_state"] = [](const Value& p) {
		int pattern = PatternIndex(p);
		bool solidified = PATTERN_API->IsPatternPieceSolidify(pattern);
		Value r = Value::object()
			.set("pattern_index", pattern)
			.set("position_2d", Value::from(PATTERN_API->GetPatternPiecePos(pattern)))
			.set("layer", PATTERN_API->GetPatternLayer(pattern))
			.set("solidified", solidified);
		if (solidified) // CLO returns an uninitialised value otherwise
			r.set("solidify_strength", PATTERN_API->GetPatternPieceSolidifyStrengthen(pattern));
		return r.set("grain_degrees", PATTERN_API->GetPatternPieceGrainDirection(pattern))
			.set("shrinkage_percent", Value::from(PATTERN_API->GetShrinkagePercentage(pattern)))
			.set("arrangement", Value::from(PATTERN_API->GetArrangementOfPattern(pattern)));
	};
	h["remove_all_pins"] = [](const Value&) {
		int before = PATTERN_API->GetPinListSize();
		bool ok = PATTERN_API->RemoveAllPins();
		return Value::object().set("removed", ok).set("pins_before", before).set("pins_after", PATTERN_API->GetPinListSize());
	};

	// -- 3D placement --
	h["get_arrangement_points"] = [](const Value&) {
		std::vector<std::map<std::string, std::string>> list = PATTERN_API->GetArrangementList();
		Value points = Value::array();
		for (size_t i = 0; i < list.size(); ++i)
		{
			Value item = Value::from(list[i]);
			item.set("arrangement_index", (int)i);
			points.a.push_back(item);
		}
		return Value::object().set("arrangement_points", points).set("count", (int)list.size());
	};
	h["place_pattern"] = [](const Value& p) {
		int pattern = PatternIndex(p);
		Value r = Value::object().set("pattern_index", pattern);
		if (p.has("arrangement_index"))
		{
			int index = I(p, "arrangement_index");
			int count = (int)PATTERN_API->GetArrangementList().size();
			if (index < 0 || index >= count)
				throw std::runtime_error("arrangement_index is out of range (" + std::to_string(count) +
										 " arrangement points; load an avatar to get them)");
			PATTERN_API->SetArrangement(pattern, index);
			r.set("arrangement_index", index);
		}
		if (p.has("orientation")) { int v = I(p, "orientation"); PATTERN_API->SetArrangementOrientation(pattern, v); r.set("orientation", v); }
		if (p.has("position_x") || p.has("position_y") || p.has("offset"))
		{
			int x = I(p, "position_x", 0), y = I(p, "position_y", 0), offset = I(p, "offset", 0);
			PATTERN_API->SetArrangementPosition(pattern, x, y, offset);
			r.set("position_x", x).set("position_y", y).set("offset", offset);
		}
		if (p.has("shape_style"))
		{
			std::string style = p.str("shape_style");
			if (style != "Flat" && style != "Curved")
				throw std::runtime_error("shape_style must be \"Flat\" or \"Curved\"");
			PATTERN_API->SetArrangementShapeStyle(pattern, style);
			r.set("shape_style", style);
		}
		r.set("arrangement", Value::from(PATTERN_API->GetArrangementOfPattern(pattern)));
		return r;
	};
	h["reset_arrangement"] = [](const Value&) {
		UTILITY_API->ResetClothArrangement();
		return Value::object().set("reset", true);
	};
	h["move_pattern_2d"] = [](const Value& p) {
		int pattern = PatternIndex(p);
		if (p.has("x") || p.has("y"))
			PATTERN_API->SetPatternPiecePos(pattern, (float)p.num("x", 0), (float)p.num("y", 0));
		else
			PATTERN_API->SetPatternPieceMove(pattern, (float)p.num("dx", 0), (float)p.num("dy", 0));
		return Value::object().set("pattern_index", pattern).set("position_2d", Value::from(PATTERN_API->GetPatternPiecePos(pattern)));
	};

	// -- Lines and shapes --
	// CLO silently creates nothing for impossible requests (e.g. an offset longer than the
	// line), so these report how many internal shapes actually appeared.
	h["add_internal_shape"] = [](const Value& p) {
		int pattern = PatternIndex(p);
		auto points = Points(p);
		int before = ChildCount(pattern);
		PATTERN_API->CreateInternalShapeWithPoints(pattern, points, p.boolean("closed", false));
		return Value::object().set("pattern_index", pattern).set("point_count", (int)points.size())
			.set("internal_shapes_created", RequireCreated(pattern, before, "check the points (at least two, in pattern coordinates)"));
	};
	h["offset_internal_line"] = [](const Value& p) {
		int pattern = PatternIndex(p), line = LineIndex(p, pattern);
		int before = ChildCount(pattern);
		PATTERN_API->OffsetAsInternalLine(pattern, line, I(p, "count", 1), (float)p.num("distance"),
										  p.boolean("reverse", false), p.boolean("extend", false));
		return Value::object().set("pattern_index", pattern).set("line_index", line)
			.set("internal_shapes_created", RequireCreated(pattern, before,
				"try reverse=true, a smaller distance, or a longer line (the offset must fit inside the piece)"));
	};
	h["convert_shape"] = [](const Value& p) {
		int pattern = PatternIndex(p), child = I(p, "internal_shape");
		std::string to = p.str("to");
		if (to == "internal")
			PATTERN_API->ConvertToInternalLine(pattern, child);
		else if (to == "base")
			PATTERN_API->ConvertToBaseLine(pattern, child);
		else
			throw std::runtime_error("'to' must be \"internal\" or \"base\"");
		return Value::object().set("pattern_index", pattern).set("internal_shape", child).set("converted_to", to);
	};
	h["delete_point"] = [](const Value& p) {
		int pattern = PatternIndex(p), point = I(p, "point_index");
		if (point < 0 || point >= LineCount(pattern))
			throw std::runtime_error("point_index is out of range");
		PATTERN_API->DeletePoint(pattern, point);
		return Value::object().set("pattern_index", pattern).set("point_index", point).set("deleted", true);
	};
	h["delete_line"] = [](const Value& p) {
		int pattern = PatternIndex(p), line = LineIndex(p, pattern);
		PATTERN_API->DeleteLine(pattern, line);
		return Value::object().set("pattern_index", pattern).set("line_index", line).set("deleted", true);
	};
	h["mirror_pattern"] = [](const Value& p) {
		int pattern = PatternIndex(p);
		int before = PATTERN_API->GetPatternCount();
		PATTERN_API->SymmetryPatternPiece(pattern, p.boolean("with_sewing", true));
		return Value::object().set("pattern_index", pattern).set("pattern_count", PATTERN_API->GetPatternCount()).set("created", PATTERN_API->GetPatternCount() > before);
	};
	h["unfold_pattern"] = [](const Value& p) {
		int pattern = PatternIndex(p), line = LineIndex(p, pattern);
		bool ok = PATTERN_API->UnfoldPatternPiece(pattern, line, p.boolean("half_symmetry", false));
		return Value::object().set("pattern_index", pattern).set("line_index", line).set("unfolded", ok);
	};

	// -- Elastic and shrinkage --
	h["set_elastic"] = [](const Value& p) {
		int pattern = PatternIndex(p), line = LineIndex(p, pattern, "line_index", true);
		Value r = Value::object().set("pattern_index", pattern).set("line_index", line);
		if (p.has("enabled")) { bool v = p.boolean("enabled", true); PATTERN_API->SetPatternPieceElastic(pattern, line, v); r.set("enabled", v); }
		if (p.has("strength")) { float v = (float)p.num("strength"); PATTERN_API->SetPatternPieceElasticStrength(pattern, line, v); r.set("strength", v); }
		if (p.has("ratio")) { int v = I(p, "ratio"); PATTERN_API->SetPatternPieceElasticStrengthRatio(pattern, line, v); r.set("ratio", v); }
		if (p.has("segment_length")) { float v = (float)p.num("segment_length"); PATTERN_API->SetPatternPieceElasticSegmentLength(pattern, line, v); r.set("segment_length", v); }
		if (p.has("total_length")) { float v = (float)p.num("total_length"); PATTERN_API->SetPatternPieceElasticTotalLength(pattern, line, v); r.set("total_length", v); }
		if (r.o.size() == 2)
			throw std::runtime_error("nothing to set; pass enabled, strength, ratio, segment_length or total_length");
		return r;
	};
	// CLO's shrinkage is the piece's size in percent: 100 = unchanged, 97 = shrinks 3 %.
	h["set_shrinkage"] = [](const Value& p) {
		int pattern = PatternIndex(p);
		for (const char* key : {"width_percent", "height_percent"})
			if (p.has(key) && (p.num(key) < 50.0 || p.num(key) > 150.0))
				throw std::runtime_error(std::string(key) + " must be between 50 and 150 (100 = no shrinkage, 97 = 3 % shrinkage)");
		if (p.has("width_percent")) PATTERN_API->SetWidthShrinkagePercentage(pattern, (float)p.num("width_percent"));
		if (p.has("height_percent")) PATTERN_API->SetHeightShrinkagePercentage(pattern, (float)p.num("height_percent"));
		return Value::object().set("pattern_index", pattern).set("shrinkage_percent", Value::from(PATTERN_API->GetShrinkagePercentage(pattern)));
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

	// Tell the client which request is running, so it keeps waiting through long simulations
	// instead of timing out (and never re-sends a request that is still executing).
	Value busy = Value::object();
	try
	{
		Value request = mj::parse(data);
		if (const Value* id = request.find("id"))
			busy.set("request_id", *id);
		busy.set("command", request.str("type", ""));
	}
	catch (...)
	{
	}
	busy.set("started", (double)time(nullptr));
	WriteStatus("busy", busy);

	std::string response = RunCommand(data);
	if (!WriteAtomic(CommFile(L"response.json"), response))
		g_lastError = "could not write response.json";
	++g_handled;
	WriteStatus("listening");
	g_lastStatus = GetTickCount();
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

// Loaded at startup as CloLibraryAPI_Plugin.dll (autostart). It only starts the listener and
// registers no menu action: when it did, CLO added its action to the Plug-in menu next to the
// Plug-in Manager entry and listed the listener three times. The menu toggle comes from the
// Plug-in Manager registration (the exported DoFunction below).
class CloMcpPlugin : public CLOAPI::LibraryWindowInterface
{
public:
	bool IsPluginEnabled() override { return false; }

	void DoFunctionStartUp() override { StartListener(); }

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
