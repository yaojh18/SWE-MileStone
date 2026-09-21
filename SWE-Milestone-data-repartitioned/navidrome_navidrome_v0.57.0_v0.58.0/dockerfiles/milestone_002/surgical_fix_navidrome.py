#!/usr/bin/env python3
"""
Surgical fix script for navidrome milestone_002 testbed.
Fixes v0.58.0 test/mock files to work with v0.57.0 interfaces.
"""
import re
import sys
import os

def fix_mock_album_repo(content):
    """Fix MockAlbumRepo Search signature"""
    # Change Search signature
    content = re.sub(
        r"func \(m \*MockAlbumRepo\) Search\(q string, offset int, size int, options \.\.\.model\.QueryOptions\)",
        "func (m *MockAlbumRepo) Search(q string, offset, size int, includeMissing bool)",
        content
    )
    # Remove options handling block
    content = re.sub(r"\tif len\(options\) > 0 \{[^}]+\}\n", "", content)
    return content

def fix_mock_artist_repo(content):
    """Fix MockArtistRepo GetIndex and Search signatures"""
    # Fix GetIndex - remove libraryIds parameter
    content = re.sub(
        r"func \(m \*MockArtistRepo\) GetIndex\(includeMissing bool, libraryIds \[\]int, roles \.\.\.model\.Role\)",
        "func (m *MockArtistRepo) GetIndex(includeMissing bool, roles ...model.Role)",
        content
    )
    # Fix Search signature
    content = re.sub(
        r"func \(m \*MockArtistRepo\) Search\(q string, offset int, size int, options \.\.\.model\.QueryOptions\)",
        "func (m *MockArtistRepo) Search(q string, offset, size int, includeMissing bool)",
        content
    )
    # Remove options handling block
    content = re.sub(r"\tif len\(options\) > 0 \{[^}]+\}\n", "", content)
    return content

def fix_mock_mediafile_repo(content):
    """Fix MockMediaFileRepo Search signature"""
    content = re.sub(
        r"func \(m \*MockMediaFileRepo\) Search\(q string, offset int, size int, options \.\.\.model\.QueryOptions\)",
        "func (m *MockMediaFileRepo) Search(q string, offset, size int, includeMissing bool)",
        content
    )
    content = re.sub(r"\tif len\(options\) > 0 \{[^}]+\}\n", "", content)
    return content

def fix_mock_library_repo(content):
    """Fix MockLibraryRepo - replace undefined model.ErrValidation"""
    content = content.replace("model.ErrValidation", 'errors.New("validation error")')
    return content

def fix_artist_repository_test(content):
    """Fix artist_repository_test.go for v0.57.0 compatibility"""

    # 1. Replace createUserWithLibraries function - match until closing brace + newline + newline
    content = re.sub(
        r"func createUserWithLibraries\(userID string, libraryIDs \[\]int\) model\.User \{[\s\S]*?\n\treturn user\n\}",
        """func createUserWithLibraries(userID string, libraryIDs []int) model.User {
	// [ENV-PATCH] Simplified for v0.57.0 compatibility - no Libraries field
	return model.User{ID: userID, UserName: userID, Name: userID}
}""",
        content
    )

    # 2. Fix GetIndex calls - remove []int{} parameter
    # GetIndex(false, []int{1}) -> GetIndex(false)
    # GetIndex(false, []int{1}, model.RoleComposer) -> GetIndex(false, model.RoleComposer)
    content = re.sub(r"GetIndex\(false, \[\]int\{[^}]*\}\)", "GetIndex(false)", content)
    content = re.sub(r"GetIndex\(false, \[\]int\{[^}]*\}, ", "GetIndex(false, ", content)

    # 3. Comment out LibraryStatsJSON usage
    content = content.replace(
        "dba.LibraryStatsJSON = string(statsJSON)",
        "// [ENV-PATCH] dba.LibraryStatsJSON = string(statsJSON)"
    )
    content = content.replace(
        "statsJSON, _ := json.Marshal(stats)",
        "_ = stats // [ENV-PATCH] statsJSON, _ := json.Marshal(stats)"
    )

    # 4. Skip the test that uses LibraryStatsJSON
    content = content.replace(
        'It("parses stats and similar artists correctly"',
        'XIt("[ENV-PATCH] parses stats and similar artists correctly"'
    )

    # 5. Comment out SetUserLibraries and GetUserLibraries calls
    content = re.sub(
        r"(\s+)err = ur\.SetUserLibraries\(",
        r"\1// [ENV-PATCH] err = ur.SetUserLibraries(",
        content
    )
    content = re.sub(
        r"(\s+)_ = ur\.SetUserLibraries\(",
        r"\1// [ENV-PATCH] _ = ur.SetUserLibraries(",
        content
    )
    content = re.sub(
        r"(\s+)libraries, err := ur\.GetUserLibraries\(",
        r"\1// [ENV-PATCH] libraries, err := ur.GetUserLibraries(",
        content
    )

    # 6. Comment out user.Libraries assignments
    content = re.sub(
        r"(\s+)unauthorizedUser\.Libraries = libraries",
        r"\1// [ENV-PATCH] unauthorizedUser.Libraries = libraries",
        content
    )

    # 7. Remove "Regular User Operations" context entirely (uses SetUserLibraries heavily)
    # Since XContext still compiles the code, we need to remove the entire block
    content = re.sub(
        r'\tContext\("Regular User Operations", func\(\) \{[\s\S]*?\n\t\}\)\n\}\)',
        '\t// [ENV-PATCH] Regular User Operations context removed - multi-library features not available in v0.57.0\n})',
        content
    )

    # 8. Skip MBID and Text Search which may use multi-library features
    content = content.replace(
        '\tDescribe("MBID and Text Search"',
        '\tXDescribe("[ENV-PATCH] MBID and Text Search"'
    )

    # 9. Fix Search calls - add false parameter
    content = re.sub(r"\.Search\(([^,]+), (\d+), (\d+)\)", r".Search(\1, \2, \3, false)", content)

    # 10. Remove unused imports that result from removing multi-library code
    content = re.sub(r'\t"encoding/json"\n', '', content)

    return content

def fix_user_repository_test(content):
    """Fix user_repository_test.go - skip multi-library tests"""
    # Remove Library Association Methods Describe block
    content = re.sub(
        r'\tDescribe\("Library Association Methods", func\(\) \{[\s\S]*?\n\t\}\)\n',
        '\t// [ENV-PATCH] Library Association Methods tests removed - not available in v0.57.0\n',
        content
    )
    # Remove Admin User Auto-Assignment Describe block (also uses library features)
    content = re.sub(
        r'\tDescribe\("Admin User Auto-Assignment", func\(\) \{[\s\S]*?\n\t\}\)\n\}\)',
        '\t// [ENV-PATCH] Admin User Auto-Assignment tests removed - multi-library features not available in v0.57.0\n})',
        content
    )
    # Remove unused imports that result from removing the multi-library code
    content = re.sub(r'\t"context"\n', '', content)
    content = re.sub(r'\t"slices"\n', '', content)
    content = re.sub(r'\t"github\.com/Masterminds/squirrel"\n', '', content)
    return content

def fix_album_repository_test(content):
    """Fix album_repository_test.go"""
    # Fix Search calls - add false parameter if missing
    content = re.sub(r"\.Search\(([^,]+), (\d+), (\d+)\)", r".Search(\1, \2, \3, false)", content)
    return content

def fix_mediafile_repository_test(content):
    """Fix mediafile_repository_test.go"""
    content = re.sub(r"mr\.Search\(([^,]+), (\d+), (\d+)\)", r"mr.Search(\1, \2, \3, false)", content)
    return content

def fix_genre_repository_test(content):
    """Fix genre_repository_test.go - remove libraryId from tagRepo.Add"""
    content = re.sub(r"tagRepo\.Add\(\d+,\s*\n", "tagRepo.Add(\n", content)
    content = re.sub(r"tagRepo\.Add\(\d+, ", "tagRepo.Add(", content)
    return content

def fix_tag_repository_test(content):
    """Fix tag_repository_test.go - remove libraryId from repo.Add"""
    # Remove libraryID from repo.Add(1, ...) calls
    content = re.sub(r"err = repo\.Add\(\d+,\s*\n", "err = repo.Add(\n", content)
    content = re.sub(r"err = repo\.Add\(\d+, ", "err = repo.Add(", content)
    content = re.sub(r"repo\.Add\(\d+,\s*\n", "repo.Add(\n", content)
    content = re.sub(r"repo\.Add\(\d+, ", "repo.Add(", content)
    return content

def add_build_ignore(content):
    """Add //go:build ignore to a file"""
    if not content.startswith("//go:build ignore"):
        content = "//go:build ignore\n\n" + content
    return content

def fix_persistence_suite_test(content):
    """Fix persistence_suite_test.go"""
    # Comment out multi-library field assignments
    content = content.replace("mf.LibraryPath = ", "// [ENV-PATCH] mf.LibraryPath = ")
    content = content.replace("mf.LibraryName = ", "// [ENV-PATCH] mf.LibraryName = ")
    content = content.replace("al.LibraryPath = ", "// [ENV-PATCH] al.LibraryPath = ")
    content = content.replace("al.LibraryName = ", "// [ENV-PATCH] al.LibraryName = ")
    # Comment out the SetUserLibraries for loop - match the specific pattern
    content = re.sub(
        r"\t// Associate users with library 1 \(default test library\)\n\tfor i := range testUsers \{[\s\S]*?ur\.SetUserLibraries[\s\S]*?\n\t\t\}\n\t\}",
        "\t// [ENV-PATCH] SetUserLibraries loop removed for v0.57.0 compatibility",
        content
    )
    # Comment out AddMediaFilesByID if it doesn't exist
    content = content.replace("plsBest.AddMediaFilesByID", "// [ENV-PATCH] plsBest.AddMediaFilesByID")
    content = content.replace("plsCool.AddMediaFilesByID", "// [ENV-PATCH] plsCool.AddMediaFilesByID")
    return content

def fix_library_test(content):
    """Fix core/library_test.go"""
    # Comment out TotalDuration if it doesn't exist
    content = content.replace(
        "Expect(libAfter.TotalDuration)",
        "// [ENV-PATCH] Expect(libAfter.TotalDuration)"
    )
    return content

def fix_library_repository_test(content):
    """Fix persistence/library_repository_test.go"""
    # Comment out TotalDuration checks
    content = content.replace(
        "Expect(libAfter.TotalDuration)",
        "// [ENV-PATCH] Expect(libAfter.TotalDuration)"
    )
    return content

def fix_playlist_repository_test(content):
    """Fix persistence/playlist_repository_test.go"""
    # Skip tests that use AddMediaFilesByID
    content = content.replace(
        'It("adds media files by ID"',
        'XIt("[ENV-PATCH] adds media files by ID"'
    )
    # Comment out AddMediaFilesByID calls
    content = re.sub(
        r"(\s+)(newPls\.AddMediaFilesByID)",
        r"\1// [ENV-PATCH] \2",
        content
    )
    return content

def fix_sql_base_repository_test(content):
    """Fix persistence/sql_base_repository_test.go"""
    # Skip tests that use applyLibraryFilter
    content = content.replace(
        '\tDescribe("applyLibraryFilter"',
        '\tXDescribe("[ENV-PATCH] applyLibraryFilter"'
    )
    return content

def fix_playlists_test(content):
    """Fix core/playlists_test.go - skip normalizePathForComparison tests"""
    # Find and replace the entire normalizePathForComparison Describe block
    content = re.sub(
        r'\tDescribe\("normalizePathForComparison", func\(\) \{.*?\n\t\}\)',
        '\tXDescribe("[ENV-PATCH] normalizePathForComparison", func() {\n\t\t// Tests skipped - function not available at v0.57.0\n\t})',
        content,
        flags=re.DOTALL
    )
    return content

def process_file(filepath, fix_func):
    """Process a file with the given fix function"""
    try:
        with open(filepath, 'r') as f:
            content = f.read()

        fixed_content = fix_func(content)

        with open(filepath, 'w') as f:
            f.write(fixed_content)

        print(f"Fixed: {filepath}")
        return True
    except Exception as e:
        print(f"Error fixing {filepath}: {e}")
        return False

def main():
    if len(sys.argv) < 3:
        print("Usage: python surgical_fix_navidrome.py <action> <filepath>")
        print("Actions: mock_album, mock_artist, mock_mediafile, mock_library,")
        print("         artist_test, user_test, album_test, mediafile_test,")
        print("         genre_test, suite_test, library_test, playlists_test")
        sys.exit(1)

    action = sys.argv[1]
    filepath = sys.argv[2]

    fix_funcs = {
        "mock_album": fix_mock_album_repo,
        "mock_artist": fix_mock_artist_repo,
        "mock_mediafile": fix_mock_mediafile_repo,
        "mock_library": fix_mock_library_repo,
        "artist_test": fix_artist_repository_test,
        "user_test": fix_user_repository_test,
        "album_test": fix_album_repository_test,
        "mediafile_test": fix_mediafile_repository_test,
        "genre_test": fix_genre_repository_test,
        "tag_test": fix_tag_repository_test,
        "suite_test": fix_persistence_suite_test,
        "library_test": fix_library_test,
        "library_repo_test": fix_library_repository_test,
        "playlist_test": fix_playlist_repository_test,
        "sql_base_test": fix_sql_base_repository_test,
        "playlists_test": fix_playlists_test,
        "build_ignore": add_build_ignore,
    }

    if action not in fix_funcs:
        print(f"Unknown action: {action}")
        sys.exit(1)

    if not process_file(filepath, fix_funcs[action]):
        sys.exit(1)

if __name__ == "__main__":
    main()
