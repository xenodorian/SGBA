/* Stand-in for libfluidsynth (MIDI synthesizer) in the ArkOS bundle.
 *
 * pygame's SDL_mixer links against FluidSynth for .mid playback, and FluidSynth in turn needs glib and libstdc++
 * from the system. SGBA games stream compressed Ogg/MP3 audio and never play MIDI, so this stub provides the
 * functions SDL_mixer imports and makes MIDI initialisation fail cleanly. It depends on nothing, so the bundle
 * needs no system libraries beyond the C library.
 */
#define STUB(name) long name(void) { return 0; }
STUB(delete_fluid_player)
STUB(delete_fluid_settings)
STUB(delete_fluid_synth)
STUB(fluid_player_add)
STUB(fluid_player_add_mem)
STUB(fluid_player_get_status)
STUB(fluid_player_play)
STUB(fluid_player_set_loop)
STUB(fluid_player_stop)
STUB(fluid_settings_getnum)
STUB(fluid_settings_setnum)
STUB(fluid_synth_get_settings)
STUB(fluid_synth_set_gain)
STUB(fluid_synth_sfload)
STUB(fluid_synth_write_float)
STUB(fluid_synth_write_s16)
STUB(new_fluid_player)
STUB(new_fluid_settings)
STUB(new_fluid_synth)
