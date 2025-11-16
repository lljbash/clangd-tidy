namespace BadNamespace {
    void BadFunction() {
        // Do something
    }
}

int main() {
    BadNamespace::BadFunction();
    return 0;
}
