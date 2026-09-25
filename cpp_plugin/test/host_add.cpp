// Reproduces what CLO's Plug-in Manager "+ ADD" does: load the DLL, read GetActionName,
// unload it, keep running the UI loop. Then loads a second copy from another path the way
// a registered plug-in is loaded later, and toggles it through DoFunction.
#include <windows.h>
#include <cstdio>
#include <string>
#include "CLOAPIInterface.h"
#include "pure_stubs.h"

struct FakeUtility : StubUtility {
	std::string GetProjectName() override { return "HarnessProject"; }
};
struct FakePattern : StubPattern {
	int GetPatternCount() override { return 2; }
	std::string GetPatternPieceName(int i) override { return "Piece " + std::to_string(i); }
};

static void Pump(int ms) {
	DWORD end = GetTickCount() + ms; MSG msg;
	while (GetTickCount() < end) {
		while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
		Sleep(5);
	}
}
static int Listeners() {
	int n = 0; HWND w = nullptr;
	while ((w = FindWindowExW(HWND_MESSAGE, w, L"CloMcpListenerWindow", nullptr))) {
		DWORD pid = 0;
		GetWindowThreadProcessId(w, &pid);
		if (pid == GetCurrentProcessId()) ++n;
	}
	return n;
}
typedef const char* (*NameFn)();
typedef void (*DoFn)();

int main(int argc, char** argv) {
	auto& api = CLOAPI::APICommand::getInstance();
	api.SetUtilityAPI(new FakeUtility);
	api.SetPatternAPI(new FakePattern);

	HMODULE dll = LoadLibraryA(argv[1]);
	printf("[add] loaded, GetActionName = '%s'\n", ((NameFn)GetProcAddress(dll, "GetActionName"))());
	BOOL freed = FreeLibrary(dll);
	HMODULE still = GetModuleHandleA(argv[1]);
	printf("[add] FreeLibrary returned %d, module still mapped: %s\n", freed, still ? "yes" : "no");
	fflush(stdout);
	Pump(1500);  // timer fires here: crashes if the DLL was really unloaded
	printf("[add] UI loop survived, listeners in process: %d\n", Listeners());

	if (argc > 2) {  // second copy from another path
		HMODULE copy = LoadLibraryA(argv[2]);
		((NameFn)GetProcAddress(copy, "GetActionName"))();
		printf("[copy] loaded second copy, listeners in process: %d\n", Listeners());
		((DoFn)GetProcAddress(copy, "DoFunction"))();
		printf("[copy] DoFunction (stop) -> listeners: %d\n", Listeners());
		((DoFn)GetProcAddress(copy, "DoFunction"))();
		printf("[copy] DoFunction (start) -> listeners: %d\n", Listeners());
	}
	fflush(stdout);
	Pump(atoi(argc > 3 ? argv[3] : "8") * 1000);
	printf("done\n");
	return 0;
}
