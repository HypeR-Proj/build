/* SPDX-FileCopyrightText: 2026 roolrz
 * SPDX-License-Identifier: Apache-2.0
 */

/* Deterministic SVR4 newc writer used by the integration build. */

#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

enum {
    CPIO_HEADER_SIZE = 110,
    COPY_BUFFER_SIZE = 16 * 1024,
    DIRECTORY_MODE = 0040000,
    REGULAR_MODE = 0100000,
};

static uint64_t output_offset;

static int fail(const char *subject)
{
    fprintf(stderr, "newc-pack: %s: %s\n", subject, strerror(errno));
    return EXIT_FAILURE;
}

static int fail_message(const char *message)
{
    fprintf(stderr, "newc-pack: %s\n", message);
    return EXIT_FAILURE;
}

static int write_bytes(const void *bytes, size_t length)
{
    errno = 0;
    if (fwrite(bytes, 1, length, stdout) != length) {
        if (errno == 0) {
            return fail_message("short write to standard output");
        }
        return fail("standard output");
    }
    output_offset += length;
    return EXIT_SUCCESS;
}

static int align_output(void)
{
    static const unsigned char zeros[3] = {0, 0, 0};
    const size_t padding = (size_t)((0 - output_offset) & 3);

    return write_bytes(zeros, padding);
}

static int path_is_canonical(const char *path)
{
    const char *component = path;

    if (*path == '\0' || *path == '/' || path[strlen(path) - 1] == '/') {
        return 0;
    }
    for (const char *cursor = path;; ++cursor) {
        if (*cursor == '/' || *cursor == '\0') {
            const size_t length = (size_t)(cursor - component);
            if (length == 0 || (length == 1 && component[0] == '.') ||
                (length == 2 && component[0] == '.' && component[1] == '.')) {
                return 0;
            }
            if (*cursor == '\0') {
                return 1;
            }
            component = cursor + 1;
        }
    }
}

static int write_header(
    uint32_t inode,
    uint32_t mode,
    uint32_t links,
    uint32_t size,
    const char *path)
{
    char header[CPIO_HEADER_SIZE + 1];
    const size_t name_size = strlen(path) + 1;
    const int length = snprintf(
        header,
        sizeof(header),
        "070701%08" PRIx32 "%08" PRIx32 "%08" PRIx32 "%08" PRIx32
        "%08" PRIx32 "%08" PRIx32 "%08" PRIx32 "%08" PRIx32
        "%08" PRIx32 "%08" PRIx32 "%08" PRIx32 "%08zx%08" PRIx32,
        inode,
        mode,
        (uint32_t)0,
        (uint32_t)0,
        links,
        (uint32_t)0,
        size,
        (uint32_t)0,
        (uint32_t)0,
        (uint32_t)0,
        (uint32_t)0,
        name_size,
        (uint32_t)0);

    if (length != CPIO_HEADER_SIZE) {
        errno = EOVERFLOW;
        return fail(path);
    }
    if (write_bytes(header, CPIO_HEADER_SIZE) != EXIT_SUCCESS ||
        write_bytes(path, name_size) != EXIT_SUCCESS) {
        return EXIT_FAILURE;
    }
    return align_output();
}

static int write_empty_entry(
    uint32_t inode,
    uint32_t mode,
    uint32_t links,
    const char *path)
{
    return write_header(inode, mode, links, 0, path);
}

static int write_file(uint32_t inode, uint32_t permissions, const char *path, const char *source)
{
    struct stat metadata;
    unsigned char buffer[COPY_BUFFER_SIZE];
    FILE *input;

    if (!path_is_canonical(path)) {
        errno = EINVAL;
        return fail(path);
    }
    if (stat(source, &metadata) != 0) {
        return fail(source);
    }
    if (!S_ISREG(metadata.st_mode) || metadata.st_size < 0 ||
        (uintmax_t)metadata.st_size > UINT32_MAX) {
        errno = EFBIG;
        return fail(source);
    }
    input = fopen(source, "rb");
    if (input == NULL) {
        return fail(source);
    }
    if (write_header(
            inode,
            REGULAR_MODE | permissions,
            1,
            (uint32_t)metadata.st_size,
            path) != EXIT_SUCCESS) {
        fclose(input);
        return EXIT_FAILURE;
    }
    uint64_t remaining = (uint64_t)metadata.st_size;
    while (remaining != 0) {
        const size_t requested =
            remaining < sizeof(buffer) ? (size_t)remaining : sizeof(buffer);
        const size_t received = fread(buffer, 1, requested, input);
        if (received != requested) {
            if (ferror(input) == 0) {
                errno = EIO;
            }
            fclose(input);
            return fail(source);
        }
        if (write_bytes(buffer, received) != EXIT_SUCCESS) {
            fclose(input);
            return EXIT_FAILURE;
        }
        remaining -= received;
    }
    if (fclose(input) != 0) {
        return fail(source);
    }
    return align_output();
}

static int parse_permissions(const char *text, uint32_t *permissions)
{
    char *end;
    errno = 0;
    const unsigned long value = strtoul(text, &end, 8);
    if (errno != 0 || *text == '\0' || *end != '\0' || value > 0777) {
        errno = EINVAL;
        return EXIT_FAILURE;
    }
    *permissions = (uint32_t)value;
    return EXIT_SUCCESS;
}

int main(int argc, char **argv)
{
    if (argc < 4 || (argc - 1) % 3 != 0) {
        fputs("usage: newc-pack MODE ARCHIVE_PATH SOURCE [MODE ARCHIVE_PATH SOURCE ...]\n", stderr);
        return EXIT_FAILURE;
    }
    if (write_empty_entry(1, DIRECTORY_MODE | 0755, 2, ".") != EXIT_SUCCESS) {
        return EXIT_FAILURE;
    }
    uint32_t inode = 2;
    for (int index = 1; index < argc; index += 3) {
        uint32_t permissions;
        if (parse_permissions(argv[index], &permissions) != EXIT_SUCCESS) {
            return fail(argv[index]);
        }
        if (write_file(inode, permissions, argv[index + 1], argv[index + 2]) != EXIT_SUCCESS) {
            return EXIT_FAILURE;
        }
        ++inode;
    }
    if (write_empty_entry(inode, 0, 1, "TRAILER!!!") != EXIT_SUCCESS ||
        fflush(stdout) != 0) {
        return fail("standard output");
    }
    return EXIT_SUCCESS;
}
