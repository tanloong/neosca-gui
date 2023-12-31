#!/usr/bin/env python3

try:
    from xml.etree.ElementTree import XML, fromstring
except ImportError:
    from xml.etree.ElementTree import XML, fromstring
import glob
import logging
import os.path as os_path
import sys
import zipfile
from os import PathLike
from typing import Any, ByteString, Callable, Dict, Iterable, Optional, Set, Union

from charset_normalizer import detect

from neosca_gui.ng_platform_info import IS_WINDOWS
from neosca_gui.ng_util import SCAProcedureResult


class SCAIO:
    SUPPORTED_EXTENSIONS = ("txt", "docx", "odt")

    DOCX_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    DOCX_PARA = DOCX_NAMESPACE + "p"
    DOCX_TEXT = DOCX_NAMESPACE + "t"

    ODT_NAMESPACE = ".//{urn:oasis:names:tc:opendocument:xmlns:text:1.0}"
    ODT_PARA = ODT_NAMESPACE + "p"

    def __init__(self):
        self.extension_readfunc_map: Dict[str, Callable] = {
            extension: getattr(self, f"read_{extension}") for extension in self.SUPPORTED_EXTENSIONS
        }
        self.previous_encoding: str = "utf-8"

    def read_docx(self, path: str) -> str:
        """
        Take the path of a docx file as argument, return the text in unicode.
        This approach does not extract text from headers and footers.

        https://etienned.github.io/posts/extract-text-from-word-docx-simply/
        """
        with zipfile.ZipFile(path) as zip_file:
            xml_content = zip_file.read("word/document.xml")
        tree = XML(xml_content)

        paragraphs = []
        for paragraph in tree.iter(self.DOCX_PARA):
            text = "".join(node.text for node in paragraph.iter(self.DOCX_TEXT) if node.text)
            paragraphs.append(text)
        return "\n".join(paragraphs)

    def read_odt(self, path: str) -> str:
        with zipfile.ZipFile(path) as zip_file:
            xml_content = zip_file.read("content.xml")
        root = fromstring(xml_content)
        paragraphs = root.findall(self.ODT_PARA)
        return "\n".join("".join(node.itertext()) for node in paragraphs)

    def _read_txt(self, path: str, mode: str, encoding: Optional[str] = None) -> Union[str, ByteString]:
        try:
            with open(path, mode=mode, encoding=encoding) as f:
                content = f.read()
        # input file existence has already been checked in main.py, here check
        # it again in case users remove input files during runtime
        except FileNotFoundError:
            logging.critical(f"{path} does not exist.")
            sys.exit(1)
        else:
            return content

    def read_txt(self, path: str, is_guess_encoding: bool = True) -> Optional[str]:
        if not is_guess_encoding:
            return self._read_txt(path, "r", "utf-8")  # type:ignore

        try:
            logging.info(f"Attempting to read {path} with {self.previous_encoding} encoding...")
            content = self._read_txt(path, "r", self.previous_encoding)  # type:ignore
        except UnicodeDecodeError:
            logging.info(f"Attempt failed. Reading {path} in binary mode...")
            bytes_ = self._read_txt(path, "rb")
            logging.info("Guessing the encoding of the byte string...")
            encoding = detect(bytes_)["encoding"]  # type:ignore

            if encoding is None:
                logging.warning(f"{path} is of unsupported file type. Skipped.")
                return None

            self.previous_encoding = encoding  # type:ignore
            logging.info(f"Decoding the byte string with {encoding} encoding...")
            content = bytes_.decode(encoding=encoding)  # type:ignore

        return content  # type:ignore

    @classmethod
    def suffix(cls, path: str) -> str:
        *_, extension = path.split(".")
        return extension

    def read_file(self, path: str) -> Optional[str]:
        extension = self.suffix(path)
        if extension not in self.SUPPORTED_EXTENSIONS:
            logging.warning(f"[SCAIO] {path} is of unsupported filetype. Skipping.")
            return None

        return self.extension_readfunc_map[extension](path)

    @classmethod
    def is_writable(cls, filename: str) -> SCAProcedureResult:
        """check whether files are opened by such other processes as WPS"""
        if not os_path.exists(filename):
            return True, None
        try:
            with open(filename, "w", encoding="utf-8"):
                pass
        except PermissionError:
            return (
                False,
                (
                    f"PermissionError: can not write to {filename}, because it is already in use"
                    " by another process.\n\n1. Ensure that {filename} is closed, or \n2."
                    " Specify another output filename through the `-o` option, e.g. nsca"
                    f" input.txt -o {filename.replace('.csv', '-2.csv')}"
                ),
            )
        else:
            return True, None

    @classmethod
    def load_pickle_lzma_file(cls, file_path: Union[str, PathLike], default: Any = None) -> dict:
        import lzma
        import pickle

        if os_path.isfile(file_path) and os_path.getsize(file_path) > 0:
            with open(file_path, "rb") as f:
                data_pickle_lzma = f.read()

            data_pickle = lzma.decompress(data_pickle_lzma)
            return pickle.loads(data_pickle)
        else:
            return default

    @classmethod
    def load_pickle_file(cls, file_path: Union[str, PathLike], default: Any = None) -> Any:
        import pickle

        if os_path.isfile(file_path) and os_path.getsize(file_path) > 0:
            with open(file_path, "rb") as f:
                data_pickle = f.read()
            return pickle.loads(data_pickle)
        else:
            return default

    @classmethod
    def load_lzma_file(cls, file_path: Union[str, PathLike], default: Any = None) -> bytes:
        import lzma

        if os_path.isfile(file_path) and os_path.getsize(file_path) > 0:
            with open(file_path, "rb") as f:
                data_lzma = f.read()

            data = lzma.decompress(data_lzma)
            return data
        else:
            return default

    def get_verified_ifile_list(self, ifile_list: Iterable[str]) -> Set[str]:
        verified_ifile_list = []
        for path in ifile_list:
            if os_path.isfile(path):
                extension = self.suffix(path)
                if extension not in self.SUPPORTED_EXTENSIONS:
                    logging.warning(f"[SCAIO] {path} is of unsupported filetype. Skipping.")
                    continue
                logging.debug(f"[SCAIO] Adding {path} to input file list")
                verified_ifile_list.append(path)
            elif os_path.isdir(path):
                verified_ifile_list.extend(
                    path
                    for path in glob.glob(f"{path}{os_path.sep}*")
                    if os_path.isfile(path) and self.suffix(path) in self.SUPPORTED_EXTENSIONS
                )
            elif glob.glob(path):
                verified_ifile_list.extend(glob.glob(path))
            else:
                logging.critical(f"No such file as\n\n{path}")
                sys.exit(1)
        if IS_WINDOWS:
            verified_ifile_list = [
                path
                for path in verified_ifile_list
                if not (path.endswith(".docx") and os_path.basename(path).startswith("~"))
            ]
        return set(verified_ifile_list)

    @classmethod
    def has_valid_cache(cls, file_path: str, cache_path: str) -> bool:
        ret = False
        is_exist = os_path.exists(cache_path)
        if is_exist:
            is_not_empty = os_path.getsize(cache_path) > 0
            is_cache_newer_than_input = os_path.getmtime(cache_path) > os_path.getmtime(file_path)
            if is_not_empty and is_cache_newer_than_input:
                ret = True
        return ret
