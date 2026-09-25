// Stand-in for CLO: real CLOAPIInterface.dll, fake Utility/Pattern APIs (Fabric/Export left
// null on purpose), loads CloMcpPlugin.dll via Create() and runs a normal Win32 event loop.
#include <windows.h>
#include <cstdio>
#include <string>
#include "CLOAPIInterface.h"
#include "LibraryWindowInterface.h"
#include "pure_stubs.h"

struct FakeUtility : StubUtility {
	std::string GetProjectName() override { return "HarnessProject"; }
	unsigned int GetMajorVersion() override { return 2025; }
	unsigned int GetMinorVersion() override { return 2; }
	unsigned int GetPatchVersion() override { return 236; }
};
struct FakePattern : StubPattern {
	int GetPatternCount() override { return 2; }
	std::string GetPatternPieceName(int i) override { return "Piece " + std::to_string(i); }
};

int main(int argc, char** argv) {
	int seconds = argc > 2 ? atoi(argv[2]) : 20;
	auto& api = CLOAPI::APICommand::getInstance();
	api.SetUtilityAPI(new FakeUtility);
	api.SetPatternAPI(new FakePattern);

	HMODULE dll = LoadLibraryA(argv[1]);
	if (!dll) { printf("LoadLibrary failed %lu\n", GetLastError()); return 1; }
	auto create = (CLOAPI::LibraryWindowInterface* (*)())GetProcAddress(dll, "Create");
	CLOAPI::LibraryWindowInterface* plugin = create();
	// The autostart (library) entry point must not register a menu action: CLO would list the
	// listener again next to the Plug-in Manager entry.
	printf("plugin loaded: menu action enabled=%d, action name=%s\n", plugin->IsPluginEnabled(),
		   plugin->GetActionName() ? plugin->GetActionName() : "(none)");
	fflush(stdout);

	unsigned long long loops = 0; DWORD worst = 0, last = GetTickCount();
	DWORD end = GetTickCount() + seconds * 1000;
	MSG msg;
	while (GetTickCount() < end) {   // like CLO's UI loop: never blocks for long
		while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) { TranslateMessage(&msg); DispatchMessageW(&msg); }
		DWORD now = GetTickCount(); if (now - last > worst) worst = now - last; last = now;
		Sleep(5); ++loops;
	}
	printf("host UI loop iterations: %llu, longest gap between iterations: %lu ms\n", loops, worst);
	return 0;
}
